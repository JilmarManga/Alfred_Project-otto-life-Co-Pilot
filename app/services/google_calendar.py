import datetime
from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from app.services.morning_brief.message_builder import format_time_human

SCOPES = ["https://www.googleapis.com/auth/calendar"]


class CalendarTokenInvalid(Exception):
    """Stored refresh token can no longer be exchanged for an access token
    (revoked by user, expired due to OAuth-app Testing-mode 7-day TTL, password
    change, scope change, 6-month inactivity). Callers should clear the user's
    credentials and route them through the reconnect flow."""

# Function to normalize events into a consistent format
def normalize_events(events):
    normalized = []

    for event in events:
        start = event["start"].get("dateTime", event["start"].get("date"))
        end = event["end"].get("dateTime", event["end"].get("date"))

        title = event.get("summary", "No title").strip()
        location = event.get("location", None)
        description = event.get("description", None)

        meeting_link = None

        # Extract link (basic version)
        if description and "http" in description:
            meeting_link = description

        # Detect virtual vs physical
        is_virtual = False
        if meeting_link:
            is_virtual = True
        elif location and ("zoom" in location.lower() or "meet" in location.lower()):
            is_virtual = True

        normalized.append({
            "title": title,
            "start": start,
            "end": end,
            "location": location,
            "is_virtual": is_virtual,
            "meeting_link": meeting_link,
        })

    return normalized


# Function to summarize the day in a human-friendly way
def summarize_day(events):
    if not events:
        return "Hoy no tienes eventos."

    total = len(events)

    # Sort events by start time
    events_sorted = sorted(events, key=lambda x: x["start"])
    first_event_time = events_sorted[0]["start"]
    last_event_time = events_sorted[-1]["start"]

    # Extract hour:minute
    first_time = format_time_human(first_event_time, "es")
    last_time = format_time_human(last_event_time, "es")

    if total == 1:
        return f"Tienes 1 evento hoy. Empieza a las {first_time}"

    return f"Tienes {total} eventos hoy. El primero empieza a las {first_time}"


# Function to format events in a detailed way
def format_events_detailed(events):
    detailed_list = []

    for e in events:
        time_part = format_time_human(e["start"], "es")

        if e["is_virtual"]:
            detail = f"{time_part} — {e['title']} (virtual)"
            if e["meeting_link"]:
                detail += f" → {e['meeting_link']}"
        else:
            location = e["location"] if e["location"] else "sin ubicación"
            detail = f"{time_part} — {e['title']} ({location})"

        detailed_list.append(detail)

    return "\n".join(detailed_list)


# Extra function to describe the next event in a more human-friendly way
def describe_next_event(events):
    if not events:
        return "No tienes nada pendiente."

    events_sorted = sorted(events, key=lambda x: x["start"])
    next_event = events_sorted[0]

    title = next_event["title"]
    time_part = next_event["start"].split("T")[1][:5]

    return f"Hoy tienes {title} a las {time_part}"


# ---- Per-user OAuth helpers (onboarding V1.0.0) -------------------------

import os


def get_calendar_service_for_user(refresh_token: str):
    """
    Build a Calendar API service from a per-user refresh token.
    Used by onboarding callback and any per-user calendar flow.
    """
    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.getenv("GOOGLE_OAUTH_CLIENT_ID"),
        client_secret=os.getenv("GOOGLE_OAUTH_CLIENT_SECRET"),
        scopes=SCOPES,
    )
    try:
        creds.refresh(Request())
    except RefreshError as exc:
        raise CalendarTokenInvalid(str(exc)) from exc
    return build("calendar", "v3", credentials=creds)


def get_today_events_for_user(refresh_token: str):
    """Fetch today's events for a specific user by their refresh token."""
    service = get_calendar_service_for_user(refresh_token)

    now = datetime.datetime.utcnow().isoformat() + "Z"
    end = (datetime.datetime.utcnow() + datetime.timedelta(days=1)).isoformat() + "Z"

    events_result = service.events().list(
        calendarId="primary",
        timeMin=now,
        timeMax=end,
        singleEvents=True,
        orderBy="startTime",
    ).execute()

    return events_result.get("items", [])


def create_event_for_user(
    refresh_token: str,
    *,
    title: str,
    start_iso: str,
    end_iso: str,
    timezone_str: str,
    location: str = None,
) -> dict:
    """
    Create a calendar event on the user's primary calendar.
    Returns the created event dict from Google (includes 'id', 'htmlLink', 'start', etc.).

    start_iso / end_iso: ISO 8601 datetime strings. If they include a tz offset
    Google honors it; timezone_str is provided as a fallback for floating times.
    """
    service = get_calendar_service_for_user(refresh_token)

    body = {
        "summary": title,
        "start": {"dateTime": start_iso, "timeZone": timezone_str},
        "end": {"dateTime": end_iso, "timeZone": timezone_str},
    }
    if location:
        body["location"] = location

    return service.events().insert(calendarId="primary", body=body).execute()


def get_upcoming_events_window(
    refresh_token: str,
    minutes_from: int,
    minutes_to: int,
) -> list:
    """
    Fetch events starting within [now + minutes_from, now + minutes_to] (UTC).
    Used by the 1-hour reminder cron (typical window: 55–75 min).
    Returns raw Google event dicts (callers pull id/summary/start/location).
    """
    service = get_calendar_service_for_user(refresh_token)

    now = datetime.datetime.utcnow()
    time_min = (now + datetime.timedelta(minutes=minutes_from)).isoformat() + "Z"
    time_max = (now + datetime.timedelta(minutes=minutes_to)).isoformat() + "Z"

    events_result = service.events().list(
        calendarId="primary",
        timeMin=time_min,
        timeMax=time_max,
        singleEvents=True,
        orderBy="startTime",
    ).execute()

    return events_result.get("items", [])