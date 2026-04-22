import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.agents.travel_agent.skill_context import SkillContext, SkillResult
from app.agents.travel_agent.skills.base import TravelSkill
from app.repositories.scheduled_reminder_repository import ScheduledReminderRepository

logger = logging.getLogger(__name__)


class ScheduleDepartureReminderSkill(TravelSkill):
    """Persists a one-off departure reminder to Firestore.

    fire_at is computed as event_start - duration_minutes so it survives
    Railway restarts and is delivered by the 15-min cron in cron_routes.py.
    """
    name = "schedule_departure_reminder"

    def execute(self, ctx: SkillContext) -> SkillResult:
        user = ctx.user
        phone = user.get("phone_number", "")
        lang = (user.get("language") or "es").lower()

        pending = ctx.payload.get("pending_travel", {})
        event_title = pending.get("event_title", "Evento")
        event_start_iso: Optional[str] = pending.get("event_start_iso")
        resolved_location: Optional[str] = pending.get("resolved_location", "")
        duration_minutes: Optional[int] = pending.get("duration_minutes")

        if not event_start_iso or not duration_minutes:
            logger.warning(
                "ScheduleDepartureReminderSkill: missing event_start_iso or duration for %s",
                phone,
            )
            return SkillResult(success=False, error_message="reminder_data_incomplete")

        try:
            event_start = datetime.fromisoformat(event_start_iso)
            if event_start.tzinfo is None:
                event_start = event_start.replace(tzinfo=timezone.utc)
            fire_at = event_start - timedelta(minutes=duration_minutes)
            fire_at_iso = fire_at.isoformat()
        except (ValueError, TypeError) as exc:
            logger.warning("ScheduleDepartureReminderSkill: bad event_start_iso %r: %s", event_start_iso, exc)
            return SkillResult(success=False, error_message="reminder_data_incomplete")

        try:
            ScheduledReminderRepository.create(
                user_phone_number=phone,
                reminder_type="departure",
                event_title=event_title,
                event_location=resolved_location or "",
                event_start_iso=event_start_iso,
                fire_at_iso=fire_at_iso,
                lang=lang,
            )
        except Exception as exc:
            logger.exception("ScheduleDepartureReminderSkill: Firestore write failed for %s: %s", phone, exc)
            return SkillResult(success=False, error_message="reminder_save_failed")

        return SkillResult(
            success=True,
            data={
                "type": "travel_reminder_confirmed",
                "leave_at_display": pending.get("leave_at_display", ""),
                "event_title": event_title,
            },
        )
