from datetime import datetime, timezone

from app.agents.base_agent import BaseAgent
from app.models.parsed_message import ParsedMessage
from app.models.agent_result import AgentResult
from app.services.google_calendar import get_today_events_for_user, normalize_events, summarize_day, format_events_detailed
from app.services.maps.maps_service import estimate_travel_info
from app.services.weather.weather_service import get_weather_for_today
from app.services.token_crypto import decrypt
from app.db.user_context_store import get_user_context, update_user_context


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
            refresh_token = self._get_refresh_token(user)
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
