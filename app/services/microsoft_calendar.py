"""Microsoft Graph calendar — provider sibling of `google_calendar.py`.

Returns **Google-shaped raw event dicts** so every existing consumer
(`normalize_events`, the cron raw-shape reader, the agents) works unchanged:

    {"id", "summary", "location", "description",
     "start": {"dateTime": <iso+offset>}, "end": {"dateTime": <iso+offset>}}

Raises the existing `CalendarTokenInvalid` (imported from google_calendar) on
a dead/revoked refresh token so the shared reconnect flow handles it.
"""
import datetime
import logging
from typing import Optional

import requests

from app.services.google_calendar import CalendarTokenInvalid
from app.services.microsoft_oauth import GRAPH_SCOPES, _build_app

logger = logging.getLogger(__name__)

_GRAPH = "https://graph.microsoft.com/v1.0"
_TIMEOUT = 15


def _access_token(refresh_token: str) -> str:
    """Redeem the stored refresh token for a Graph access token.

    Raises CalendarTokenInvalid when Microsoft rejects the refresh token
    (revoked, expired, password change, consent withdrawn)."""
    app = _build_app()
    result = app.acquire_token_by_refresh_token(refresh_token, scopes=GRAPH_SCOPES)
    if "access_token" not in result:
        err = result.get("error", "")
        desc = result.get("error_description", "")
        # invalid_grant / interaction_required ⇒ token is dead.
        raise CalendarTokenInvalid(f"microsoft refresh failed ({err}: {desc[:200]})")
    return result["access_token"]


def _to_iso(graph_dt: Optional[dict]) -> Optional[str]:
    """Graph returns {'dateTime': '2026-05-17T15:00:00.0000000', 'timeZone': 'UTC'}.
    We always request UTC (Prefer header), so emit a clean tz-aware ISO string
    that datetime.fromisoformat() accepts."""
    if not graph_dt:
        return None
    raw = graph_dt.get("dateTime")
    if not raw:
        return None
    # Trim Graph's 7-digit fractional seconds → microseconds, append UTC offset.
    base = raw.split(".")[0]
    try:
        dt = datetime.datetime.fromisoformat(base)
    except ValueError:
        return raw
    return dt.replace(tzinfo=datetime.timezone.utc).isoformat()


def _normalize_graph_event(ev: dict) -> dict:
    """Map a Graph event onto the Google raw-event shape used everywhere."""
    location = None
    loc = ev.get("location") or {}
    if loc.get("displayName"):
        location = loc["displayName"]

    # Surface an online-meeting URL through `description` so the existing
    # normalize_events() link/virtual detection keeps working.
    description = None
    online = ev.get("onlineMeeting") or {}
    join_url = online.get("joinUrl") or ev.get("onlineMeetingUrl")
    if join_url:
        description = join_url
    elif ev.get("bodyPreview"):
        description = ev["bodyPreview"]

    return {
        "id": ev.get("id"),
        "summary": (ev.get("subject") or "").strip(),
        "location": location,
        "description": description,
        "start": {"dateTime": _to_iso(ev.get("start"))},
        "end": {"dateTime": _to_iso(ev.get("end"))},
    }


def _calendar_view(refresh_token: str, start_utc: datetime.datetime,
                    end_utc: datetime.datetime) -> list:
    token = _access_token(refresh_token)
    params = {
        "startDateTime": start_utc.replace(microsecond=0).isoformat() + "Z",
        "endDateTime": end_utc.replace(microsecond=0).isoformat() + "Z",
        "$orderby": "start/dateTime",
        "$select": "id,subject,start,end,location,bodyPreview,onlineMeeting,onlineMeetingUrl",
        "$top": "50",
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Prefer": 'outlook.timezone="UTC"',
    }
    resp = requests.get(
        f"{_GRAPH}/me/calendarView", params=params, headers=headers, timeout=_TIMEOUT
    )
    if resp.status_code == 401:
        raise CalendarTokenInvalid("microsoft graph returned 401")
    resp.raise_for_status()
    return [_normalize_graph_event(e) for e in resp.json().get("value", [])]


def get_today_events_for_user(refresh_token: str) -> list:
    """Today's events (now → +1 day), Google-shaped."""
    now = datetime.datetime.utcnow()
    return _calendar_view(refresh_token, now, now + datetime.timedelta(days=1))


def get_upcoming_events_window(
    refresh_token: str, minutes_from: int, minutes_to: int
) -> list:
    """Events starting within [now+minutes_from, now+minutes_to] (UTC)."""
    now = datetime.datetime.utcnow()
    return _calendar_view(
        refresh_token,
        now + datetime.timedelta(minutes=minutes_from),
        now + datetime.timedelta(minutes=minutes_to),
    )


def create_event_for_user(
    refresh_token: str,
    *,
    title: str,
    start_iso: str,
    end_iso: str,
    timezone_str: str,
    location: str = None,
) -> dict:
    """Create an event on the user's default Microsoft calendar.
    Returns the Graph event JSON (callers only read 'id')."""
    token = _access_token(refresh_token)
    body = {
        "subject": title,
        "start": {"dateTime": start_iso, "timeZone": timezone_str},
        "end": {"dateTime": end_iso, "timeZone": timezone_str},
    }
    if location:
        body["location"] = {"displayName": location}

    resp = requests.post(
        f"{_GRAPH}/me/events",
        json=body,
        headers={"Authorization": f"Bearer {token}"},
        timeout=_TIMEOUT,
    )
    if resp.status_code == 401:
        raise CalendarTokenInvalid("microsoft graph returned 401")
    resp.raise_for_status()
    return resp.json()
