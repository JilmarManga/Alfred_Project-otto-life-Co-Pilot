import logging
from datetime import datetime, timedelta, timezone

from app.agents.reminder_agent._shared.time_resolver import resolve_fire_at
from app.agents.reminder_agent.skill_context import SkillContext, SkillResult
from app.agents.reminder_agent.skills.base import ReminderSkill
from app.repositories.user_reminder_repository import UserReminderRepository

logger = logging.getLogger(__name__)


class RescheduleReminderSkill(ReminderSkill):
    """Gate-only: act on the post-delivery follow-up reply.

    payload = {doc_id, mode, reminder_text, tz, reminder_time?, reminder_period?}
    mode ∈ {"new_time", "in_an_hour", "delete"}.
    """

    name = "reschedule_reminder"

    def execute(self, ctx: SkillContext) -> SkillResult:
        payload = ctx.payload or {}
        doc_id = payload.get("doc_id")
        mode = payload.get("mode")
        if not doc_id or mode not in {"new_time", "in_an_hour", "delete"}:
            return SkillResult(success=False, error_message="reminder_create_failed")

        reminder_text = payload.get("reminder_text") or ""
        tz_name = payload.get("tz") or (ctx.user or {}).get("timezone") or "UTC"
        now_utc = datetime.now(timezone.utc)

        if mode == "delete":
            UserReminderRepository.delete(doc_id)
            return SkillResult(
                success=True,
                data={"type": "reminder_followup_dismissed"},
            )

        if mode == "in_an_hour":
            new_fire = (now_utc + timedelta(hours=1)).isoformat()
            UserReminderRepository.reschedule(doc_id, new_fire)
            return SkillResult(
                success=True,
                data={
                    "type": "reminder_rescheduled",
                    "reminder_text": reminder_text,
                    "fire_at": new_fire,
                },
            )

        # mode == "new_time"
        fire_at_iso, status = resolve_fire_at(
            reminder_time=payload.get("reminder_time"),
            reminder_period=payload.get("reminder_period"),
            tz_name=tz_name,
            now_utc=now_utc,
        )
        if status != "resolved" or not fire_at_iso:
            return SkillResult(success=False, error_message="reminder_create_failed")
        UserReminderRepository.reschedule(doc_id, fire_at_iso)
        return SkillResult(
            success=True,
            data={
                "type": "reminder_rescheduled",
                "reminder_text": reminder_text,
                "fire_at": fire_at_iso,
            },
        )
