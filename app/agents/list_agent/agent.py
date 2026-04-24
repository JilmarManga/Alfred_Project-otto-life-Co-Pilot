import logging
import re
from typing import Optional

from app.agents.base_agent import BaseAgent
from app.agents.list_agent.skill_context import SkillContext, SkillResult
from app.agents.list_agent.skills.confirm_delete_list import ConfirmDeleteListSkill
from app.agents.list_agent.skills.delete_list import DeleteListSkill
from app.agents.list_agent.skills.recall_list import RecallListSkill
from app.agents.list_agent.skills.save_to_list import SaveToListSkill
from app.models.agent_result import AgentResult
from app.models.parsed_message import ParsedMessage

logger = logging.getLogger(__name__)


# Pattern triggers used by `ListAgent.matches()`. Kept separate from the
# parser's `_scan_signals` keyword sets per ticket §3 — lists do NOT use the
# signals mechanism.
_SAVE_TRIGGERS = {
    # Spanish
    "guarda", "guardar", "guárdame", "guardame",
    "añade a mi lista", "anade a mi lista",
    "agrégalo a mi lista", "agregalo a mi lista",
    "ponlo en mi lista", "mete a mi lista",
    # English
    "save this", "keep track of",
    "add to my list", "add this to my list", "put this in my list",
}
_RECALL_TRIGGERS = {
    # Spanish
    "muéstrame la lista", "muestrame la lista",
    "dame mi lista", "mi lista de",
    # English
    "show me my list", "show my list", "give me my list", "my list of",
}
# Delete requires a noun after the verb so "elimina el arriendo" still trips
# the pattern — the skill then returns `list_not_found` if no list matches.
_DELETE_PREFIXES_RE = re.compile(
    r"^\s*(delete|elimina|borra)\s+\S+", re.IGNORECASE,
)


class ListAgent(BaseAgent):
    """Domain agent for user-defined named lists (save / recall / delete).

    Public contract (matches every other agent):
        execute(parsed: ParsedMessage, user: dict) -> AgentResult

    Extra public classmethod:
        matches(parsed: ParsedMessage) -> bool
    Called by the router's pattern predicate. Verb-presence is enough;
    skill composition happens in `_pick_skill_from_router`, not here.

    Internal dispatch mirrors TravelAgent:
        _pick_skill_from_router -> skill name (str | None)
        run_skill(name, ctx)    -> AgentResult  [called by gates that bypass Layer 2]
        _run(name, ctx)         -> AgentResult  [shared execution path]
    """

    agent_name = "ListAgent"

    _SKILLS = {
        "save_to_list":        SaveToListSkill,
        "recall_list":         RecallListSkill,
        "delete_list":         DeleteListSkill,
        "confirm_delete_list": ConfirmDeleteListSkill,
    }

    # ------------------------------------------------------------------ #
    # Public entry points                                                  #
    # ------------------------------------------------------------------ #

    @classmethod
    def matches(cls, parsed: ParsedMessage) -> bool:
        """Deterministic pattern check: does this message look like a list op?"""
        if parsed.list_intent in {"save", "recall", "delete"}:
            return True
        text = (parsed.raw_message or "").lower().strip()
        if not text:
            return False
        if any(kw in text for kw in _SAVE_TRIGGERS):
            return True
        if any(kw in text for kw in _RECALL_TRIGGERS):
            return True
        if _DELETE_PREFIXES_RE.match(text):
            return True
        return False

    def execute(self, parsed: ParsedMessage, user: dict) -> AgentResult:
        """Layer 2 (router) entry. Picks a skill and executes it via _run."""
        skill_name = self._pick_skill_from_router(parsed, user)
        if skill_name is None:
            # Save intent with no extractable item → ask the user to resend.
            return AgentResult(
                agent_name=self.agent_name,
                success=False,
                error_message="missing_item",
            )
        ctx = SkillContext(
            user=user,
            parsed=parsed,
            inbound_text=parsed.raw_message,
            payload={},
        )
        return self._run(skill_name, ctx)

    def run_skill(self, skill_name: str, ctx: SkillContext) -> AgentResult:
        """Gate entry. Handlers that already know which skill they want call
        this directly, bypassing Layer 1 (parse) and Layer 2 (route)."""
        return self._run(skill_name, ctx)

    # ------------------------------------------------------------------ #
    # Internal helpers                                                     #
    # ------------------------------------------------------------------ #

    def _pick_skill_from_router(
        self, parsed: ParsedMessage, user: dict,
    ) -> Optional[str]:
        """Map parsed fields + trigger phrases → skill name.

        Returns None when the message is clearly a save intent but no item
        content was extracted — `execute` converts that to a `missing_item`
        AgentResult so Layer 4 can prompt the user to resend.
        """
        intent = parsed.list_intent
        text = (parsed.raw_message or "").lower()

        if intent == "recall":
            return "recall_list"
        if intent == "delete":
            return "delete_list"
        if intent == "save":
            return "save_to_list" if parsed.list_item else None

        # No LLM-extracted intent — fall back to trigger scan.
        if any(kw in text for kw in _RECALL_TRIGGERS):
            return "recall_list"
        if _DELETE_PREFIXES_RE.match(text):
            return "delete_list"
        if any(kw in text for kw in _SAVE_TRIGGERS):
            return "save_to_list" if parsed.list_item else None

        # Defensive: router only dispatches here when matches() returned True,
        # so this branch is effectively unreachable. Treat as save-without-item.
        return None

    def _run(self, skill_name: str, ctx: SkillContext) -> AgentResult:
        skill_cls = self._SKILLS.get(skill_name)
        if skill_cls is None:
            logger.error("ListAgent: unknown skill %r", skill_name)
            return AgentResult(
                agent_name=self.agent_name,
                success=False,
                error_message=f"unknown_skill:{skill_name}",
            )
        try:
            result: SkillResult = skill_cls().execute(ctx)
        except Exception as exc:
            logger.exception("ListAgent skill %r raised: %s", skill_name, exc)
            return AgentResult(
                agent_name=self.agent_name,
                success=False,
                error_message=str(exc),
            )
        return AgentResult(
            agent_name=self.agent_name,
            success=result.success,
            data=result.data,
            error_message=result.error_message,
        )
