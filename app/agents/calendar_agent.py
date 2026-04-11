from app.agents.base_agent import BaseAgent
from app.models.parsed_message import ParsedMessage
from app.models.agent_result import AgentResult
from app.services.google_calendar import get_today_events, normalize_events, summarize_day, format_events_detailed
from app.services.maps.maps_service import estimate_travel_info
from app.db.user_context_store import get_user_context, update_user_context  # swapped for Firestore in Phase 4


class CalendarAgent(BaseAgent):

    def execute(self, parsed: ParsedMessage, user: dict) -> AgentResult:
        try:
            phone = user.get("phone_number", "")
            event_ref = parsed.event_reference

            if event_ref is not None:
                return self._handle_followup(parsed, user, phone, event_ref)

            return self._handle_query(phone)

        except Exception as e:
            return AgentResult(
                agent_name="CalendarAgent",
                success=False,
                error_message=str(e),
            )

    def _handle_query(self, phone: str) -> AgentResult:
        events_raw = get_today_events()
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

    def _handle_followup(self, parsed: ParsedMessage, user: dict, phone: str, event_ref) -> AgentResult:
        context = get_user_context(phone)
        events = context.get("today_events", [])
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
