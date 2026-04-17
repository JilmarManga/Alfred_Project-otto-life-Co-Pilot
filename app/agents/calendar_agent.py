import logging
from datetime import datetime, timedelta, timezone

from app.agents.base_agent import BaseAgent
from app.models.parsed_message import ParsedMessage
from app.models.agent_result import AgentResult
from app.services.google_calendar import (
    get_today_events_for_user,
    normalize_events,
    summarize_day,
    format_events_detailed,
    create_event_for_user,
)
from app.services.maps.maps_service import estimate_travel_info
from app.services.weather.weather_service import get_weather_for_today
from app.services.token_crypto import decrypt
from app.db.user_context_store import get_user_context, update_user_context
from app.parser.message_parser import (
    CREATE_KEYWORDS,
    REMINDER_OFF_KEYWORDS,
    REMINDER_ON_KEYWORDS,
)
from app.repositories.user_repository import UserRepository

logger = logging.getLogger(__name__)

# Hardcoded per-language copy for the two-message confirmation. The follow-up
# is sent as a separate WhatsApp message after the main confirmation.
_FOLLOW_UP_COPY = {
    "es": "¿Quieres más detalles? 🐙",
    "en": "Want more details? 🐙",
}


def _find_next_upcoming_event(events: list) -> dict | None:
    """Return the next event whose start time is after now. Falls back to first event."""
    now = datetime.now(timezone.utc)
    upcoming = []
    for event in events:
        try:
            dt = datetime.fromisoformat(event.get("start", ""))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if dt > now:
                upcoming.append((dt, event))
        except Exception:
            continue
    if upcoming:
        return min(upcoming, key=lambda x: x[0])[1]
    return events[0] if events else None


class CalendarAgent(BaseAgent):

    def _get_refresh_token(self, user: dict) -> str:
        encrypted = user.get("google_calendar_refresh_token")
        if not encrypted:
            raise ValueError("calendar_not_connected")
        return decrypt(encrypted)

    def execute(self, parsed: ParsedMessage, user: dict) -> AgentResult:
        try:
            phone = user.get("phone_number", "")
            signals = set(parsed.signals or [])

            # Reminder toggle is a settings change — no calendar API needed.
            # Handle before _get_refresh_token so a disconnected user can still
            # toggle the preference.
            if signals & REMINDER_OFF_KEYWORDS:
                return self._handle_reminder_toggle(phone, enabled=False)
            if signals & REMINDER_ON_KEYWORDS:
                return self._handle_reminder_toggle(phone, enabled=True)

            refresh_token = self._get_refresh_token(user)

            has_create_kw = bool(signals & CREATE_KEYWORDS)
            has_event_fields = bool(parsed.event_title and parsed.event_start)

            # 1. Clear creation: verb + fields
            if has_create_kw and has_event_fields:
                return self._handle_creation(parsed, user, refresh_token)

            # 2. Create verb but missing details
            if has_create_kw and not has_event_fields:
                return AgentResult(
                    agent_name="CalendarAgent",
                    success=False,
                    error_message="missing_event_details",
                )

            # 3. Ambiguous: fields extracted without a creation verb → ask user
            if has_event_fields:
                return self._handle_clarify_creation(parsed, user, phone)

            # 4. Existing paths (unchanged)
            event_ref = parsed.event_reference
            if event_ref is not None:
                return self._handle_followup(parsed, user, phone, event_ref, refresh_token)

            return self._handle_query(phone, refresh_token)

        except ValueError as e:
            if str(e) == "calendar_not_connected":
                return AgentResult(
                    agent_name="CalendarAgent",
                    success=False,
                    error_message="calendar_not_connected",
                )
            return AgentResult(
                agent_name="CalendarAgent",
                success=False,
                error_message=str(e),
            )
        except Exception as e:
            return AgentResult(
                agent_name="CalendarAgent",
                success=False,
                error_message=str(e),
            )

    def _handle_query(self, phone: str, refresh_token: str) -> AgentResult:
        events_raw = get_today_events_for_user(refresh_token)
        events = normalize_events(events_raw) if events_raw else []

        update_user_context(phone, "today_events", events)
        update_user_context(phone, "last_intent", "calendar_query")

        return AgentResult(
            agent_name="CalendarAgent",
            success=True,
            data={
                "type": "calendar_query",
                "event_count": len(events),
                "events": events,
                "summary": summarize_day(events),
                "detailed": format_events_detailed(events),
            },
        )

    def _handle_followup(self, parsed: ParsedMessage, user: dict, phone: str, event_ref, refresh_token: str) -> AgentResult:
        context = get_user_context(phone)
        events = context.get("today_events", [])

        if not events:
            events = normalize_events(get_today_events_for_user(refresh_token) or [])
            update_user_context(phone, "today_events", events)

        # "Next event" — find upcoming event + add weather
        if event_ref.time_reference == "next":
            return self._handle_next_event(user, events, refresh_token)

        # Specific event by ordinal (second, tercero, etc.)
        selected_event = None
        if event_ref.index is not None and 0 <= event_ref.index < len(events):
            selected_event = events[event_ref.index]
            update_user_context(phone, "last_referenced_event", selected_event)

        if selected_event is None:
            selected_event = context.get("last_referenced_event")

        if not selected_event:
            return AgentResult(
                agent_name="CalendarAgent",
                success=False,
                error_message="No encontré ese evento.",
            )

        title = selected_event.get("title", "Evento")
        start = selected_event.get("start", "")
        location = selected_event.get("location")

        travel_data = {}
        if location:
            user_origin = user.get("location", "Bogotá, Colombia")
            leave_at, duration_minutes = estimate_travel_info(
                destination=location,
                departure_time_iso=start,
                origin=user_origin,
            )
            travel_data = {
                "leave_at": leave_at,
                "duration_minutes": duration_minutes,
            }

        return AgentResult(
            agent_name="CalendarAgent",
            success=True,
            data={
                "type": "calendar_followup",
                "title": title,
                "start": start,
                "location": location,
                **travel_data,
            },
        )

    def _handle_next_event(self, user: dict, events: list, refresh_token: str) -> AgentResult:
        if not events:
            events = normalize_events(get_today_events_for_user(refresh_token) or [])

        event = _find_next_upcoming_event(events)
        if not event:
            return AgentResult(
                agent_name="CalendarAgent",
                success=False,
                error_message="No hay más eventos hoy.",
            )

        title = event.get("title", "Evento")
        start = event.get("start", "")
        location = event.get("location")
        user_origin = user.get("location", "Bogotá, Colombia")
        lang = user.get("language", "es")

        travel_data = {}
        if location:
            leave_at, duration_minutes = estimate_travel_info(
                destination=location,
                departure_time_iso=start,
                origin=user_origin,
            )
            travel_data = {"leave_at": leave_at, "duration_minutes": duration_minutes}

        weather = get_weather_for_today(user_city=user_origin, lang=lang)

        return AgentResult(
            agent_name="CalendarAgent",
            success=True,
            data={
                "type": "calendar_next_event",
                "title": title,
                "start": start,
                "location": location,
                "weather_summary": weather.get("summary"),
                "weather_temperature": weather.get("temperature"),
                **travel_data,
            },
        )

    def _handle_creation(self, parsed: ParsedMessage, user: dict, refresh_token: str) -> AgentResult:
        try:
            start_dt = datetime.fromisoformat(parsed.event_start)
        except (ValueError, TypeError):
            logger.warning("Invalid event_start from parser: %r", parsed.event_start)
            return AgentResult(
                agent_name="CalendarAgent",
                success=False,
                error_message="missing_event_details",
            )

        duration = parsed.event_duration_minutes or 60
        end_dt = start_dt + timedelta(minutes=duration)

        tz_str = user.get("timezone") or "UTC"
        lang = (user.get("language") or "es").lower()

        try:
            event = create_event_for_user(
                refresh_token,
                title=parsed.event_title,
                start_iso=start_dt.isoformat(),
                end_iso=end_dt.isoformat(),
                timezone_str=tz_str,
                location=parsed.event_location,
            )
        except Exception as exc:
            logger.exception("Calendar event creation failed: %s", exc)
            return AgentResult(
                agent_name="CalendarAgent",
                success=False,
                error_message="create_failed",
            )

        follow_up = _FOLLOW_UP_COPY.get(lang, _FOLLOW_UP_COPY["es"])

        return AgentResult(
            agent_name="CalendarAgent",
            success=True,
            data={
                "type": "calendar_create",
                "title": parsed.event_title,
                "start": start_dt.isoformat(),
                "location": parsed.event_location,
                "event_id": event.get("id"),
                "follow_up_message": follow_up,
            },
        )

    def _handle_clarify_creation(self, parsed: ParsedMessage, user: dict, phone: str) -> AgentResult:
        """
        LLM extracted event fields but no CREATE keyword was found — ambiguous
        between "do I have this?" and "create this". Stash the extracted event
        in user_context_store and ask the user to confirm. The next message is
        intercepted by pending_event_handler (Step 5b).
        """
        update_user_context(phone, "pending_event", {
            "title": parsed.event_title,
            "start": parsed.event_start,
            "location": parsed.event_location,
            "duration_minutes": parsed.event_duration_minutes,
        })

        return AgentResult(
            agent_name="CalendarAgent",
            success=True,
            data={
                "type": "calendar_clarify_create",
                "title": parsed.event_title,
                "start": parsed.event_start,
                "location": parsed.event_location,
            },
        )

    def _handle_reminder_toggle(self, phone: str, *, enabled: bool) -> AgentResult:
        """Flip calendar_reminders_enabled. No calendar API call required."""
        try:
            UserRepository.set_calendar_reminders_enabled(phone, enabled)
        except Exception as exc:
            logger.exception("Reminder toggle failed for %s: %s", phone, exc)
            return AgentResult(
                agent_name="CalendarAgent",
                success=False,
                error_message="reminder_toggle_failed",
            )

        return AgentResult(
            agent_name="CalendarAgent",
            success=True,
            data={
                "type": "reminder_opt_in" if enabled else "reminder_opt_out",
            },
        )
