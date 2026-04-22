import logging
from datetime import datetime, timezone

from app.agents.travel_agent.skill_context import SkillContext, SkillResult
from app.agents.travel_agent.skills.base import TravelSkill
from app.agents.travel_agent._shared.event_selection import find_next_upcoming_event
from app.agents.travel_agent._shared.leave_time import compute_leave_decision
from app.db.user_context_store import get_user_context, update_user_context
from app.services.google_calendar import get_today_events_for_user, normalize_events
from app.services.maps.maps_service import estimate_travel_info

logger = logging.getLogger(__name__)


class NextEventTravelSkill(TravelSkill):
    """Computes leave time for the user's next calendar event.

    Preserves the behaviour of the original travel_agent.execute() 1:1,
    with two targeted fixes:
    - tz-aware datetime comparison (was naive — could raise on real Google events)
    - stashes pending_travel when event has no location so the gate can resolve it
    """
    name = "next_event_travel"

    def execute(self, ctx: SkillContext) -> SkillResult:
        user = ctx.user
        phone = user.get("phone_number", "")
        user_origin = user.get("location", "Bogotá, Colombia")
        context = get_user_context(phone)

        # 1. Prefer last referenced event from a Calendar follow-up
        selected_event = context.get("last_referenced_event")

        # 2. Fallback: find next upcoming event from cached or fresh list
        if not selected_event:
            events = context.get("today_events", [])

            if not events:
                refresh_token = ctx.payload.get("refresh_token", "")
                try:
                    events_raw = get_today_events_for_user(refresh_token)
                    events = normalize_events(events_raw) if events_raw else []
                    update_user_context(phone, "today_events", events)
                except Exception as exc:
                    logger.error("NextEventTravelSkill: calendar fetch failed for %s: %s", phone, exc)
                    return SkillResult(success=False, error_message=str(exc))

            if not events:
                return SkillResult(success=True, data={"status": "no_events"})

            selected_event = find_next_upcoming_event(
                events, now=datetime.now(timezone.utc)
            )

        if not selected_event:
            return SkillResult(success=True, data={"status": "no_events"})

        start_raw = selected_event.get("start")
        location = selected_event.get("location")
        title = selected_event.get("title", "Evento")
        event_id = selected_event.get("id")

        if not location:
            # Stash state so the gate can catch the user's reply with a place name.
            update_user_context(phone, "pending_travel", {
                "step": "awaiting_location",
                "event_title": title,
                "event_start_iso": start_raw,
                "event_id": event_id,
            })
            return SkillResult(success=True, data={"status": "no_location", "title": title})

        leave_at_str, duration_minutes = estimate_travel_info(
            destination=location,
            departure_time_iso=start_raw,
            origin=user_origin,
        )

        if not leave_at_str or not duration_minutes:
            return SkillResult(
                success=True,
                data={"status": "maps_unavailable", "title": title, "location": location},
            )

        leave_decision, minutes_until_leave = compute_leave_decision(leave_at_str)

        return SkillResult(
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
