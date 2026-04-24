import logging
from datetime import datetime, timezone

from app.agents.list_agent.skill_context import SkillContext, SkillResult
from app.agents.list_agent.skills.base import ListSkill
from app.db.user_context_store import update_user_context
from app.repositories.list_repository import ListRepository

logger = logging.getLogger(__name__)


class DeleteListSkill(ListSkill):
    """Stage a list deletion by asking the user to confirm.

    Deletion is destructive, so we never auto-pick a target list. An explicit
    case-insensitive name match is required. On a successful match we stash
    `pending_list.awaiting_delete_confirmation` and return
    `list_delete_confirm` so Layer 4 can ask "¿confirmas?". The actual delete
    happens in `ConfirmDeleteListSkill`, which the gate runs on "sí"/"yes".
    """

    name = "delete_list"

    def execute(self, ctx: SkillContext) -> SkillResult:
        phone = (ctx.user or {}).get("phone_number")
        if not phone:
            logger.error("DeleteListSkill: missing phone_number in user dict")
            return SkillResult(success=False, error_message="delete_failed")

        parsed = ctx.parsed
        requested_name = (parsed.list_name if parsed else None) or None

        existing_lists = ListRepository.get_user_lists(phone)
        existing_names = [lst.get("name") for lst in existing_lists if lst.get("name")]

        if not requested_name:
            return SkillResult(
                success=False,
                error_message="list_not_found",
                data={
                    "requested_name": None,
                    "existing_names": existing_names,
                },
            )

        key = requested_name.strip().lower()
        target = next(
            (lst for lst in existing_lists if (lst.get("name_lower") or "") == key),
            None,
        )
        if not target:
            return SkillResult(
                success=False,
                error_message="list_not_found",
                data={
                    "requested_name": requested_name,
                    "existing_names": existing_names,
                },
            )

        list_id = target.get("id")
        list_name = target.get("name")
        item_count = len(target.get("items") or [])

        update_user_context(phone, "pending_list", {
            "step": "awaiting_delete_confirmation",
            "list_id": list_id,
            "list_name": list_name,
            "item_count": item_count,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })

        return SkillResult(
            success=True,
            data={
                "type": "list_delete_confirm",
                "list_name": list_name,
                "item_count": item_count,
                "list_id": list_id,
            },
        )
