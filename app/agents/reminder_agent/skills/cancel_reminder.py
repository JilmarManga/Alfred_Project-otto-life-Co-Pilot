import logging
from datetime import datetime, timezone

from app.agents.reminder_agent._shared.time_resolver import fold
from app.agents.reminder_agent.skill_context import SkillContext, SkillResult
from app.agents.reminder_agent.skills.base import ReminderSkill
from app.db.user_context_store import update_user_context
from app.repositories.user_reminder_repository import UserReminderRepository

logger = logging.getLogger(__name__)

_ACTIVE = {"scheduled", "awaiting_followup"}


class CancelReminderSkill(ReminderSkill):
    """Cancel a reminder by free-text reference.

    Never auto-picks across an ambiguous match (destructive — mirrors
    DeleteListSkill). Gate bypass: `ctx.payload["resolved_doc_id"]` deletes
    that exact doc (the awaiting_cancel_choice gate path).
    """

    name = "cancel_reminder"

    def execute(self, ctx: SkillContext) -> SkillResult:
        phone = (ctx.user or {}).get("phone_number")
        if not phone:
            logger.error("CancelReminderSkill: missing phone_number in user dict")
            return SkillResult(success=False, error_message="reminder_create_failed")

        payload = ctx.payload or {}
        parsed = ctx.parsed

        # Gate bypass: user already chose which one.
        resolved_id = payload.get("resolved_doc_id")
        if resolved_id:
            doc = UserReminderRepository.get(resolved_id)
            UserReminderRepository.delete(resolved_id)
            return SkillResult(
                success=True,
                data={
                    "type": "reminder_cancelled",
                    "reminder_text": (doc or {}).get("reminder_text")
                    or payload.get("reminder_text") or "",
                },
            )

        ref = payload.get("reminder_cancel_ref")
        if ref is None and parsed is not None:
            ref = parsed.reminder_cancel_ref or parsed.reminder_text

        docs = [
            d for d in UserReminderRepository.list_for_phone(phone)
            if d.get("status") in _ACTIVE
        ]
        existing = [d.get("reminder_text") for d in docs if d.get("reminder_text")]

        ref_folded = fold(ref) if ref else ""
        if ref_folded:
            matches = [d for d in docs if ref_folded in fold(d.get("reminder_text"))]
        else:
            matches = list(docs)

        if not matches:
            return SkillResult(
                success=False,
                error_message="reminder_not_found",
                data={"existing": existing},
            )

        if len(matches) > 1:
            candidates = [
                {"id": d.get("id"), "reminder_text": d.get("reminder_text")}
                for d in matches
            ]
            update_user_context(phone, "pending_reminder", {
                "step": "awaiting_cancel_choice",
                "candidates": candidates,
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
            return SkillResult(
                success=True,
                data={"type": "reminder_cancel_choice", "candidates": candidates},
            )

        target = matches[0]
        UserReminderRepository.delete(target.get("id"))
        return SkillResult(
            success=True,
            data={
                "type": "reminder_cancelled",
                "reminder_text": target.get("reminder_text") or "",
            },
        )
