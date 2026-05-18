"""Provider-agnostic calendar accessor.

Every calendar consumer (CalendarAgent, TravelAgent, cron reminders, morning
brief, pending-event gate) calls these helpers instead of `google_calendar`
directly. With two accounts connected, reads are **merged**; creation goes to
the **primary** account.

Returns Google-shaped raw event dicts (the Microsoft service already maps onto
that shape) so downstream `normalize_events()` / cron raw readers are unchanged.

Resilience: one dead/secondary account never breaks the merged response. A
dead **primary** token raises `CalendarTokenInvalid` (with `.provider` set) so
the agent can route the user through the correct reconnect link — but only
when `strict_primary=True` (agent path). Cron passes `strict_primary=False`
so a bad token never aborts the batch.
"""
import logging
from typing import List, Optional

from app.services import google_calendar, microsoft_calendar
from app.services.google_calendar import CalendarTokenInvalid
from app.services.token_crypto import decrypt

logger = logging.getLogger(__name__)

_PROVIDERS = {
    "google": google_calendar,
    "microsoft": microsoft_calendar,
}


def iter_calendar_accounts(user: dict) -> List[dict]:
    """Decrypted connected accounts: [{provider, refresh_token, is_primary, email}].

    Legacy-compat (no migration): a pre-existing Google-only user with just
    the flat `google_calendar_refresh_token` is surfaced as a single primary
    Google account. Accounts whose token can't be decrypted are logged and
    skipped so one corrupt entry can't break everything.
    """
    raw = user.get("connected_accounts")
    if not raw:
        legacy = user.get("google_calendar_refresh_token")
        if not legacy:
            return []
        raw = [{"provider": "google", "refresh_token": legacy,
                "is_primary": True, "email": None}]

    out: List[dict] = []
    for acc in raw:
        enc = acc.get("refresh_token")
        provider = acc.get("provider")
        if not enc or provider not in _PROVIDERS:
            continue
        try:
            token = decrypt(enc)
        except Exception as exc:
            logger.exception(
                "calendar_accounts: decrypt failed for %s account: %s",
                provider, exc,
            )
            continue
        out.append({
            "provider": provider,
            "refresh_token": token,
            "is_primary": bool(acc.get("is_primary")),
            "email": acc.get("email"),
        })

    # Guarantee a primary even if the flag is missing on legacy/odd docs.
    if out and not any(a["is_primary"] for a in out):
        out[0]["is_primary"] = True
    return out


def primary_account(user: dict) -> Optional[dict]:
    accounts = iter_calendar_accounts(user)
    for a in accounts:
        if a["is_primary"]:
            return a
    return accounts[0] if accounts else None


def _merge(user: dict, fn_name: str, *args, strict_primary: bool) -> list:
    accounts = iter_calendar_accounts(user)
    if not accounts:
        raise ValueError("calendar_not_connected")

    events: list = []
    for acc in accounts:
        service = _PROVIDERS[acc["provider"]]
        try:
            fn = getattr(service, fn_name)
            events.extend(fn(acc["refresh_token"], *args) or [])
        except CalendarTokenInvalid as exc:
            if acc["is_primary"] and strict_primary:
                exc.provider = acc["provider"]
                raise
            logger.warning(
                "calendar_accounts: %s token invalid for %s — skipping (merge continues)",
                acc["provider"], "primary" if acc["is_primary"] else "secondary",
            )
        except Exception as exc:
            # Graph/Google outage on one account must not kill the merge.
            logger.exception(
                "calendar_accounts: %s on %s account failed: %s",
                fn_name, acc["provider"], exc,
            )
    return events


def get_today_events_merged(user: dict, *, strict_primary: bool = True) -> list:
    """Today's events across all connected accounts (Google-shaped, merged)."""
    return _merge(user, "get_today_events_for_user", strict_primary=strict_primary)


def get_upcoming_events_window_merged(
    user: dict, minutes_from: int, minutes_to: int,
    *, strict_primary: bool = False,
) -> list:
    """Upcoming-window events across all accounts. Defaults to non-strict —
    the cron caller never wants one bad token to abort the run."""
    return _merge(
        user, "get_upcoming_events_window", minutes_from, minutes_to,
        strict_primary=strict_primary,
    )


def create_event_on_primary(user: dict, **kwargs) -> dict:
    """Create an event on the primary account's calendar.

    Raises ValueError('calendar_not_connected') when nothing is connected, or
    CalendarTokenInvalid (with `.provider`) when the primary token is dead so
    the agent can trigger the correct reconnect link.
    """
    acc = primary_account(user)
    if acc is None:
        raise ValueError("calendar_not_connected")
    service = _PROVIDERS[acc["provider"]]
    try:
        return service.create_event_for_user(acc["refresh_token"], **kwargs)
    except CalendarTokenInvalid as exc:
        exc.provider = acc["provider"]
        raise
