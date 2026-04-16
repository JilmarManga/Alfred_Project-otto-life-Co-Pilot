from datetime import datetime
from app.agents.base_agent import BaseAgent
from app.models.parsed_message import ParsedMessage
from app.models.agent_result import AgentResult
from app.services.google_calendar import get_today_events_for_user, normalize_events
from app.services.maps.maps_service import estimate_travel_info
from app.services.token_crypto import decrypt
from app.db.user_context_store import get_user_context, update_user_context


class TravelAgent(BaseAgent):

    def execute(self, parsed: ParsedMessage, user: dict) -> AgentResult:
        try:
            phone = user.get("phone_number", "")
            user_origin = user.get("location", "Bogotá, Colombia")
            context = get_user_context(phone)

            encrypted = user.get("google_calendar_refresh_token")
            if not encrypted:
                return AgentResult(
                    agent_name="TravelAgent",
                    success=False,
                    error_message="calendar_not_connected",
                )
            refresh_token = decrypt(encrypted)

            # 1. Prefer last referenced event (best UX — user already mentioned it)
            selected_event = context.get("last_referenced_event")

            # 2. Fallback: find next upcoming event from today's list
            if not selected_event:
                events = context.get("today_events", [])

                if not events:
                    events_raw = get_today_events_for_user(refresh_token)
                    events = normalize_events(events_raw) if events_raw else []
                    update_user_context(phone, "today_events", events)

                if not events:
                    return AgentResult(
                        agent_name="TravelAgent",
                        success=True,
                        data={"status": "no_events"},
                    )

                now = datetime.now()
                for event in events:
                    start_raw = event.get("start")
                    if not start_raw:
                        continue
                    try:
                        if datetime.fromisoformat(start_raw) > now:
                            selected_event = event
                            break
                    except Exception:
                        continue

                # If all events are past, use the last one
                if not selected_event:
                    selected_event = events[-1]

            start_raw = selected_event.get("start")
            location = selected_event.get("location")
            title = selected_event.get("title", "Evento")

            if not location:
                return AgentResult(
                    agent_name="TravelAgent",
                    success=True,
                    data={"status": "no_location", "title": title},
                )

            leave_at_str, duration_minutes = estimate_travel_info(
                destination=location,
                departure_time_iso=start_raw,
                origin=user_origin,
            )

            if not leave_at_str or not duration_minutes:
                return AgentResult(
                    agent_name="TravelAgent",
                    success=True,
                    data={"status": "maps_unavailable", "title": title, "location": location},
                )

            # Compute "should I leave now?" decision
            now = datetime.now()
            leave_decision = "unknown"
            minutes_until_leave = None
            try:
                leave_at_dt = datetime.strptime(leave_at_str, "%I:%M %p").replace(
                    year=now.year, month=now.month, day=now.day
                )
                diff = (leave_at_dt - now).total_seconds() / 60
                minutes_until_leave = int(diff)

                if diff > 10:
                    leave_decision = "not_yet"
                elif 0 <= diff <= 10:
                    leave_decision = "leave_now"
                else:
                    leave_decision = "late"
            except Exception:
                pass

            return AgentResult(
                agent_name="TravelAgent",
                success=True,
                data={
                    "status": "ok",
                    "title": title,
                    "location": location,
                    "leave_at": leave_at_str,
                    "duration_minutes": duration_minutes,
                    "leave_decision": leave_decision,
                    "minutes_until_leave": minutes_until_leave,
                },
            )

        except Exception as e:
            return AgentResult(
                agent_name="TravelAgent",
                success=False,
                error_message=str(e),
            )
