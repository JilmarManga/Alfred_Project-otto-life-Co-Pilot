import logging

from app.agents.reminder_agent.skill_context import SkillContext, SkillResult
from app.agents.reminder_agent.skills.base import ReminderSkill
from app.repositories.user_reminder_repository import UserReminderRepository

logger = logging.getLogger(__name__)

_ACTIVE = {"scheduled", "awaiting_followup"}


class ListRemindersSkill(ReminderSkill):
    """Return the user's active reminders, soonest first."""

    name = "list_reminders"

    def execute(self, ctx: SkillContext) -> SkillResult:
        phone = (ctx.user or {}).get("phone_number")
        if not phone:
            logger.error("ListRemindersSkill: missing phone_number in user dict")
            return SkillResult(success=False, error_message="reminder_create_failed")

        docs = [
            d for d in UserReminderRepository.list_for_phone(phone)
            if d.get("status") in _ACTIVE
        ]
        docs.sort(key=lambda d: d.get("fire_at") or "")

        if not docs:
            return SkillResult(success=True, data={"type": "reminder_list_empty"})

        return SkillResult(
            success=True,
            data={
                "type": "reminder_list",
                "reminders": [
                    {
                        "id": d.get("id"),
                        "reminder_text": d.get("reminder_text"),
                        "fire_at": d.get("fire_at"),
                    }
                    for d in docs
                ],
            },
        )
