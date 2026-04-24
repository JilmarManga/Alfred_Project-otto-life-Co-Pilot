import logging

from app.agents.list_agent.skill_context import SkillContext, SkillResult
from app.agents.list_agent.skills.base import ListSkill
from app.repositories.list_repository import ListRepository

logger = logging.getLogger(__name__)


class RecallListSkill(ListSkill):
    """Return the items of a named list.

    Resolution rules:
      - explicit name → case-insensitive lookup; non-existent → `list_not_found`
      - no name + 0 lists                                   → `list_not_found`
      - no name + 1 list                                    → recall that one
      - no name + 2+ lists                                  → `list_not_found`
        (responder shows the user's existing list names so they can pick)

    An existing list with zero items returns `empty_list` so the responder can
    say "esa lista está vacía" instead of rendering a blank numbered list.
    """

    name = "recall_list"

    def execute(self, ctx: SkillContext) -> SkillResult:
        phone = (ctx.user or {}).get("phone_number")
        if not phone:
            logger.error("RecallListSkill: missing phone_number in user dict")
            return SkillResult(success=False, error_message="save_failed")

        parsed = ctx.parsed
        requested_name = (parsed.list_name if parsed else None) or None

        existing_lists = ListRepository.get_user_lists(phone)
        existing_names = [lst.get("name") for lst in existing_lists if lst.get("name")]

        target = None
        if requested_name:
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
        else:
            if len(existing_lists) == 1:
                target = existing_lists[0]
            else:
                # 0 or 2+ lists — can't auto-pick. Layer 4's list_not_found copy
                # handles both cases (empty list of names vs "which one?").
                return SkillResult(
                    success=False,
                    error_message="list_not_found",
                    data={
                        "requested_name": None,
                        "existing_names": existing_names,
                    },
                )

        items = list(target.get("items") or [])
        if not items:
            return SkillResult(
                success=False,
                error_message="empty_list",
                data={"list_name": target.get("name")},
            )

        return SkillResult(
            success=True,
            data={
                "type": "list_recall",
                "list_name": target.get("name"),
                "items": items,
            },
        )
