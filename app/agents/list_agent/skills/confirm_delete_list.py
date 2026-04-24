import logging

from app.agents.list_agent.skill_context import SkillContext, SkillResult
from app.agents.list_agent.skills.base import ListSkill
from app.repositories.list_repository import ListRepository

logger = logging.getLogger(__name__)


class ConfirmDeleteListSkill(ListSkill):
    """Execute a staged list deletion.

    Reached only via the `pending_list.awaiting_delete_confirmation` gate
    step, which invokes `run_skill("confirm_delete_list", ctx)` with
    `ctx.payload = {"list_id": ..., "list_name": ...}`. Those payload values
    come from the stash set by `DeleteListSkill`, so the skill itself does
    no resolution — it just deletes the doc by id and reports.
    """

    name = "confirm_delete_list"

    def execute(self, ctx: SkillContext) -> SkillResult:
        payload = ctx.payload or {}
        list_id = payload.get("list_id")
        list_name = payload.get("list_name")

        if not list_id:
            logger.error("ConfirmDeleteListSkill: missing list_id in payload")
            return SkillResult(success=False, error_message="delete_failed")

        ok = ListRepository.delete_list(list_id)
        if not ok:
            return SkillResult(
                success=False,
                error_message="delete_failed",
                data={"list_name": list_name},
            )

        return SkillResult(
            success=True,
            data={
                "type": "list_deleted",
                "list_name": list_name,
            },
        )
