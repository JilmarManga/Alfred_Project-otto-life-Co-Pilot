import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Header, HTTPException, status

from app.handlers import onboarding_copy
from app.repositories.user_repository import UserRepository
from app.services.google_calendar import get_upcoming_events_window
from app.services.location_resolver import resolve_location, STATUS_RESOLVED
from app.services.morning_briefing import run_morning_briefing
from app.services.token_crypto import decrypt
from app.services.whatsapp_sender import send_whatsapp_message

logger = logging.getLogger(__name__)

router = APIRouter()


def _require_secret(x_cron_secret: str) -> None:
    expected = os.getenv("CRON_SHARED_SECRET")
    if not expected or x_cron_secret != expected:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or missing cron secret",
        )


def _build_authorize_url(state_token: str) -> str:
    base = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
    return f"{base}/auth/google/authorize?state={state_token}"


# --- 1-hour reminder helpers ---

_REMINDER_COPY = {
    "es": "🔔 En 1 hora: {title} — {time}",
    "en": "🔔 In 1 hour: {title} — {time}",
}


def _resolve_tz(tz_name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(tz_name) if tz_name else ZoneInfo("UTC")
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def _format_reminder_time(dt, lang: str) -> str:
    """Render an event start as human time (e.g. '3:30 pm' or '3:30 PM')."""
    formatted = dt.strftime("%I:%M %p").lstrip("0")
    return formatted.lower() if lang == "es" else formatted


def _build_reminder_message(title: str, start_dt, location: str | None, lang: str) -> str:
    time_part = _format_reminder_time(start_dt, lang)
    line = _REMINDER_COPY.get(lang, _REMINDER_COPY["en"]).format(title=title, time=time_part)
    if location:
        line += f"\n📍 {location}"
    return line


def _run_event_reminders() -> int:
    """
    For each user with calendar reminders enabled, fetch events starting in
    55–75 min and send a single WhatsApp reminder per event (deduped via
    notified_event_ids). Returns the number of reminders sent.
    """
    sent = 0
    for user in UserRepository.list_users_for_reminders():
        phone = user.get("phone")
        encrypted = user.get("google_calendar_refresh_token")
        if not phone or not encrypted:
            continue

        try:
            refresh_token = decrypt(encrypted)
        except Exception as exc:
            logger.exception("Reminder: decrypt token failed for %s: %s", phone, exc)
            continue

        try:
            events = get_upcoming_events_window(refresh_token, 55, 75) or []
        except Exception as exc:
            logger.exception("Reminder: calendar fetch failed for %s: %s", phone, exc)
            continue

        lang = (user.get("language") or "en").lower()
        tz = _resolve_tz(user.get("timezone"))
        notified = set(user.get("notified_event_ids") or [])

        for event in events:
            event_id = event.get("id")
            start_raw = (event.get("start") or {}).get("dateTime")
            if not event_id or not start_raw:
                continue  # skip all-day or malformed events

            try:
                start_dt = datetime.fromisoformat(start_raw).astimezone(tz)
            except (ValueError, TypeError):
                logger.warning("Reminder: bad start for %s/%s: %r", phone, event_id, start_raw)
                continue

            dedup_key = f"{event_id}:{start_dt.date().isoformat()}"
            if dedup_key in notified:
                continue

            title = (event.get("summary") or "").strip() or ("Evento" if lang == "es" else "Event")
            location = event.get("location")

            try:
                msg = _build_reminder_message(title, start_dt, location, lang)
                send_whatsapp_message(phone, msg)
                UserRepository.add_notified_event(phone, dedup_key)
                notified.add(dedup_key)
                sent += 1
            except Exception as exc:
                logger.exception("Reminder: send failed for %s/%s: %s", phone, event_id, exc)

    return sent


# --- Morning brief helpers ---

def _is_morning_brief_window(tz: ZoneInfo) -> bool:
    """Return True if the user's local time is between 06:00 and 06:14 (two 15-min cron ticks)."""
    local_now = datetime.now(tz)
    return local_now.hour == 6 and local_now.minute < 15


def _run_morning_briefs() -> int:
    """
    For each user with calendar connected and reminders enabled, check if it's
    their local 6:00–6:14 and they haven't received the brief today. Sends one
    brief per user per local day. Returns the number of briefs sent.
    """
    sent = 0
    for user in UserRepository.list_users_for_morning_brief():
        phone = user.get("phone")
        encrypted = user.get("google_calendar_refresh_token")
        if not phone or not encrypted:
            continue

        tz = _resolve_tz(user.get("timezone"))
        if not _is_morning_brief_window(tz):
            continue

        local_today = datetime.now(tz).date().isoformat()
        if user.get("morning_brief_sent_date") == local_today:
            continue

        try:
            refresh_token = decrypt(encrypted)
        except Exception as exc:
            logger.exception("Morning brief: decrypt token failed for %s: %s", phone, exc)
            continue

        try:
            user["_refresh_token"] = refresh_token
            run_morning_briefing(user)
            UserRepository.mark_morning_brief_sent(phone, local_today)
            sent += 1
        except Exception as exc:
            logger.exception("Morning brief: send failed for %s: %s", phone, exc)

    return sent


_DEPARTURE_REMINDER_COPY = {
    "es": "⏰ Es hora de salir para {title}",
    "en": "⏰ Time to leave for {title}",
}


def _run_departure_reminders() -> int:
    """
    Deliver one-off departure reminders whose fire_at falls within the current
    15-min window. Fires up to 15 min early (never late). Deduped via sent_at.
    Returns the number of reminders delivered.
    """
    from app.repositories.scheduled_reminder_repository import ScheduledReminderRepository

    now = datetime.now(timezone.utc)
    due = ScheduledReminderRepository.list_due_within(now, horizon_minutes=15)
    sent = 0

    for reminder in due:
        phone = reminder.get("user_phone_number")
        doc_id = reminder.get("id")
        if not phone or not doc_id:
            continue

        title = reminder.get("event_title", "")
        location = reminder.get("event_location", "")
        lang = (reminder.get("lang") or "es").lower()

        line = _DEPARTURE_REMINDER_COPY.get(lang, _DEPARTURE_REMINDER_COPY["en"]).format(title=title)
        if location:
            line += f"\n📍 {location}"

        try:
            send_whatsapp_message(phone, line)
            ScheduledReminderRepository.mark_sent(doc_id)
            sent += 1
        except Exception as exc:
            logger.exception("Departure reminder send failed for %s/%s: %s", phone, doc_id, exc)

    return sent


def run_cron_job() -> dict:
    """
    Core cron logic — called by the internal scheduler every 15 min,
    and also by the HTTP route for manual triggers. Does five things:
      1. Sends the 3h OAuth reminder to users who haven't connected yet,
         minting a fresh state token so the link is actually clickable.
      2. Retries location resolution for users whose geocoding failed during onboarding.
      3. Sends 1-hour reminders for upcoming calendar events.
      4. Sends the morning brief to users whose local time is 6:00–6:14.
      5. Delivers one-off departure reminders scheduled by TravelAgent.
    """
    followups_sent = 0
    locations_resolved = 0
    reminders_sent = 0
    morning_briefs_sent = 0
    departure_reminders_sent = 0

    # --- 1. OAuth follow-ups ---
    for user in UserRepository.list_pending_oauth_followups():
        phone = user.get("phone")
        if not phone:
            continue
        try:
            fresh_token = secrets.token_urlsafe(32)
            UserRepository.set_oauth_state_token(
                phone, fresh_token, datetime.utcnow() + timedelta(hours=1)
            )
            link = _build_authorize_url(fresh_token)
            lang = (user.get("language") or "en").lower()
            name = user.get("name") or ""
            msg = onboarding_copy.get("oauth_followup", lang, name=name, link=link)
            send_whatsapp_message(phone, msg)
            UserRepository.mark_oauth_followup_sent(phone)
            followups_sent += 1
        except Exception as exc:
            logger.exception("Failed to send oauth followup to %s: %s", phone, exc)

    # --- 2. Pending location retries ---
    for user in UserRepository.list_pending_location_retries():
        phone = user.get("phone")
        raw_city = user.get("location_raw")
        if not phone or not raw_city:
            continue
        try:
            result = resolve_location(raw_city)
            if result.status == STATUS_RESOLVED:
                UserRepository.save_resolved_location(
                    phone,
                    location=result.normalized_name,
                    latitude=result.latitude,
                    longitude=result.longitude,
                    timezone=result.timezone,
                )
                locations_resolved += 1
        except Exception as exc:
            logger.exception("Location retry failed for %s: %s", phone, exc)

    # --- 3. 1-hour calendar reminders ---
    try:
        reminders_sent = _run_event_reminders()
    except Exception as exc:
        logger.exception("Event reminders run failed: %s", exc)

    # --- 4. Morning briefs (6:00–6:14 local per user) ---
    try:
        morning_briefs_sent = _run_morning_briefs()
    except Exception as exc:
        logger.exception("Morning briefs run failed: %s", exc)

    # --- 5. One-off departure reminders ---
    try:
        departure_reminders_sent = _run_departure_reminders()
    except Exception as exc:
        logger.exception("Departure reminders run failed: %s", exc)

    logger.info(
        "Cron job complete — followups_sent=%d locations_resolved=%d reminders_sent=%d "
        "morning_briefs_sent=%d departure_reminders_sent=%d",
        followups_sent, locations_resolved, reminders_sent, morning_briefs_sent, departure_reminders_sent,
    )
    return {
        "status": "ok",
        "followups_sent": followups_sent,
        "locations_resolved": locations_resolved,
        "reminders_sent": reminders_sent,
        "morning_briefs_sent": morning_briefs_sent,
        "departure_reminders_sent": departure_reminders_sent,
    }


@router.post("/cron/oauth-followups")
async def oauth_followups(x_cron_secret: str = Header(default="")):
    """Manual trigger — protected by secret header. Calls the same logic as the internal scheduler."""
    _require_secret(x_cron_secret)
    return run_cron_job()
