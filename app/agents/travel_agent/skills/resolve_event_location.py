import logging

from app.agents.travel_agent.skill_context import SkillContext, SkillResult
from app.agents.travel_agent.skills.base import TravelSkill
from app.agents.travel_agent._shared.leave_time import compute_leave_decision
from app.services.location_resolver import (
    resolve_location,
    STATUS_RESOLVED,
    STATUS_NOT_FOUND,
    STATUS_AMBIGUOUS,
)
from app.services.maps.maps_service import estimate_travel_info

logger = logging.getLogger(__name__)


class ResolveEventLocationSkill(TravelSkill):
    """Phase 1 — keeps Otto's promise when an event has no location.

    The user replied to "dime la ubicación" with a place name. This skill:
    1. Geocodes the user's text via Google Maps Geocoding.
    2. If resolved, computes leave time via the Directions API.
    3. Returns a travel_leave_plan result with a reminder_offer flag so the
       gate can advance to step='awaiting_reminder_confirmation'.

    On any failure (geocode error, ambiguous place, Maps unavailable) it
    returns success=False with a specific error_message so the responder
    can send warm, localized copy without going to the LLM.
    """
    name = "resolve_event_location"

    def execute(self, ctx: SkillContext) -> SkillResult:
        user = ctx.user
        user_origin = user.get("location", "Bogotá, Colombia")

        pending = ctx.payload.get("pending_travel", {})
        event_title = pending.get("event_title", "Evento")
        event_start_iso = pending.get("event_start_iso")

        if not event_start_iso:
            logger.warning("ResolveEventLocationSkill: no event_start_iso in payload")
            return SkillResult(success=False, error_message="no_upcoming_event_for_location")

        place_text = (ctx.inbound_text or "").strip()
        if not place_text:
            return SkillResult(success=False, error_message="geocode_not_found")

        # 1. Geocode the user's reply
        resolution = resolve_location(place_text)

        if resolution.status == STATUS_NOT_FOUND:
            return SkillResult(success=False, error_message="geocode_not_found")

        if resolution.status == STATUS_AMBIGUOUS:
            return SkillResult(success=False, error_message="geocode_ambiguous")

        if resolution.status != STATUS_RESOLVED:
            # api_error or anything unexpected — treat as maps unavailable
            logger.warning(
                "ResolveEventLocationSkill: geocode status=%s for %r",
                resolution.status, place_text,
            )
            return SkillResult(success=False, error_message="maps_unavailable_for_place")

        resolved_location = resolution.normalized_name or place_text

        # 2. Compute travel time from user's saved origin to the resolved address
        leave_at_str, duration_minutes = estimate_travel_info(
            destination=resolved_location,
            departure_time_iso=event_start_iso,
            origin=user_origin,
        )

        if not leave_at_str or not duration_minutes:
            logger.warning(
                "ResolveEventLocationSkill: maps returned no route for %s -> %s",
                user_origin, resolved_location,
            )
            return SkillResult(success=False, error_message="maps_unavailable_for_place")

        leave_decision, minutes_until_leave = compute_leave_decision(leave_at_str)

        return SkillResult(
            success=True,
            data={
                "type": "travel_leave_plan",
                "title": event_title,
                "location": resolved_location,
                "leave_at": leave_at_str,
                "duration_minutes": duration_minutes,
                "leave_decision": leave_decision,
                "minutes_until_leave": minutes_until_leave,
                "reminder_offer": True,
            },
        )
