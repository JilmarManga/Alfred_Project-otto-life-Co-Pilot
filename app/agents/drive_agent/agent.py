"""DriveAgent — read / analyze / (confirmed) modify Google Drive files.

Follows the OTTO_AGENTS package pattern (copied from ListAgent's shape).

Safety contract:
  - Reads/analysis never mutate anything.
  - Modify is a two-phase flow: `propose_modification` computes an exact,
    deterministic change and stashes it; nothing is written until the
    pending-drive gate receives an explicit confirmation, which dispatches
    `apply_modification` via run_skill.
  - The LLM never rewrites file content. Layer 1 extracts a structured edit
    spec; the skill applies exactly that spec.

Token handling mirrors the established CalendarAgent convention in this
codebase: on a dead/missing token the agent sends the (re)connect link as a
side effect and returns a silent handled sentinel so Layer 4 emits nothing.
"""
import logging
from datetime import datetime, timezone
from typing import Optional

from app.agents.base_agent import BaseAgent
from app.agents.drive_agent.skill_context import SkillContext, SkillResult
from app.agents.drive_agent.skills.analyze_file import AnalyzeFileSkill
from app.agents.drive_agent.skills.apply_modification import ApplyModificationSkill
from app.agents.drive_agent.skills.find_file import FindFileSkill
from app.agents.drive_agent.skills.propose_modification import ProposeModificationSkill
from app.agents.drive_agent.skills.read_file import ReadFileSkill
from app.db.user_context_store import update_user_context
from app.models.agent_result import AgentResult
from app.models.parsed_message import ParsedMessage
from app.services.drive_connect import handle_drive_token_invalid, send_connect_link
from app.services.google_drive import DriveTokenInvalid

logger = logging.getLogger(__name__)

# Pattern triggers for DriveAgent.matches(). Conservative on purpose: a bare
# "archivo"/"file" would collide with benign messages, so a Drive noun is
# required — either the word "drive" or a doc/sheet noun.
_DRIVE_NOUNS = {
    "drive", "google drive",
    "documento", "documentos", "google doc", "google docs",
    "hoja de calculo", "hoja de cálculo", "spreadsheet", "google sheet",
    "google sheets", "hoja de google",
}
_DRIVE_ACTIONS = {
    # Spanish
    "lee", "leer", "léeme", "leeme", "abre", "abrir", "muéstrame el", "muestrame el",
    "busca el archivo", "busca el documento", "encuentra el archivo",
    "analiza", "analizar", "resume", "resumir", "revisa", "revisar",
    "cambia", "cambiar", "modifica", "modificar", "edita", "editar",
    "actualiza", "actualizar", "reemplaza", "reemplazar", "pon", "marca",
    # English
    "read", "open", "show me the", "find the file", "find the document",
    "analyze", "analyse", "summarize", "summarise", "review",
    "change", "modify", "edit", "update", "replace", "set", "mark",
}


def _norm(text: str) -> str:
    return (text or "").lower().strip()


class DriveAgent(BaseAgent):
    agent_name = "DriveAgent"

    # apply_modification is gate-only — never picked by _pick_skill_from_router,
    # only dispatched via run_skill after explicit user confirmation.
    _SKILLS = {
        "find_file": FindFileSkill,
        "read_file": ReadFileSkill,
        "analyze_file": AnalyzeFileSkill,
        "propose_modification": ProposeModificationSkill,
        "apply_modification": ApplyModificationSkill,
    }

    # Reverse map used to re-arm a pending file-ref ask on missing_file_ref.
    _SKILL_TO_INTENT = {
        "find_file": "find",
        "read_file": "read",
        "analyze_file": "analyze",
        "propose_modification": "modify",
    }

    # ------------------------------------------------------------------ #
    # Public entry points                                                  #
    # ------------------------------------------------------------------ #

    @classmethod
    def matches(cls, parsed: ParsedMessage) -> bool:
        """Deterministic pattern check: does this look like a Drive op?
        Verb presence is enough for skill composition later; here we only
        decide the message belongs to the Drive domain."""
        if getattr(parsed, "drive_intent", None) in {"find", "read", "analyze", "modify"}:
            return True
        text = _norm(parsed.raw_message)
        if not text:
            return False
        has_noun = any(n in text for n in _DRIVE_NOUNS)
        if not has_noun:
            return False
        return any(a in text for a in _DRIVE_ACTIONS)

    def execute(self, parsed: ParsedMessage, user: dict) -> AgentResult:
        skill_name = self._pick_skill_from_router(parsed, user)
        if skill_name is None:
            return AgentResult(
                agent_name=self.agent_name,
                success=False,
                error_message="missing_file_ref",
            )
        ctx = SkillContext(
            user=user,
            parsed=parsed,
            inbound_text=parsed.raw_message,
            payload={},
        )
        result = self._run(skill_name, ctx)

        # The user named an intent ("analyze this doc") but no filename could be
        # extracted (e.g. "ese documento"). The responder asks "which file?";
        # stash the intent so the pending-drive gate captures the next reply as
        # the filename and re-runs the ORIGINAL intent — instead of that reply
        # falling through the whole pipeline into a greeting.
        if result.error_message == "missing_file_ref":
            phone = user.get("phone_number") or user.get("phone")
            intent = self._SKILL_TO_INTENT.get(skill_name)
            if phone and intent:
                update_user_context(phone, "pending_drive", {
                    "step": "awaiting_file_ref",
                    "intent": intent,
                    "original_text": parsed.raw_message,
                    "edit_spec": getattr(parsed, "drive_edit", None),
                    "created_at": datetime.now(timezone.utc).isoformat(),
                })
        return result

    def run_skill(self, skill_name: str, ctx: SkillContext) -> AgentResult:
        """Gate entry. Handlers that already know the skill call this directly,
        bypassing Layer 1/2."""
        return self._run(skill_name, ctx)

    # ------------------------------------------------------------------ #
    # Internal helpers                                                     #
    # ------------------------------------------------------------------ #

    def _pick_skill_from_router(
        self, parsed: ParsedMessage, user: dict,
    ) -> Optional[str]:
        intent = getattr(parsed, "drive_intent", None)
        if intent == "read":
            return "read_file"
        if intent == "analyze":
            return "analyze_file"
        if intent == "find":
            return "find_file"
        if intent == "modify":
            # propose_modification is wired in Phase 3; until then a modify
            # request degrades to analyze so the user still gets value and we
            # never silently no-op (and never write anything).
            return "propose_modification" if "propose_modification" in self._SKILLS else "analyze_file"

        # No LLM-extracted intent — fall back to verb scan.
        text = _norm(parsed.raw_message)
        if any(a in text for a in ("analiza", "analizar", "resume", "resumir",
                                   "analyze", "analyse", "summarize", "summarise",
                                   "revisa", "review")):
            return "analyze_file"
        if any(a in text for a in ("busca", "encuentra", "find", "list", "lista")):
            return "find_file"
        return "read_file"

    def _run(self, skill_name: str, ctx: SkillContext) -> AgentResult:
        skill_cls = self._SKILLS.get(skill_name)
        if skill_cls is None:
            logger.error("DriveAgent: unknown skill %r", skill_name)
            return AgentResult(
                agent_name=self.agent_name,
                success=False,
                error_message=f"unknown_skill:{skill_name}",
            )

        phone = ctx.user.get("phone_number") or ctx.user.get("phone")
        lang = (ctx.user.get("language") or "es").lower()

        try:
            result: SkillResult = skill_cls().execute(ctx)
        except DriveTokenInvalid as exc:
            logger.warning("Drive token invalid for %s: %s", phone, exc)
            if phone:
                handle_drive_token_invalid(phone, lang)
            return AgentResult(
                agent_name=self.agent_name,
                success=True,
                data={"type": "drive_token_invalid_handled"},
            )
        except Exception as exc:
            logger.exception("DriveAgent skill %r raised: %s", skill_name, exc)
            return AgentResult(
                agent_name=self.agent_name,
                success=False,
                error_message=str(exc),
            )

        # Skill says the user isn't connected → send the connect link as a
        # side effect and stay silent (mirrors CalendarAgent's handled path).
        if (not result.success) and result.error_message == "drive_not_connected":
            if phone:
                send_connect_link(phone, lang)
            return AgentResult(
                agent_name=self.agent_name,
                success=True,
                data={"type": "drive_connect_link_sent"},
            )

        # Centralized disambiguation stash: when any read/analyze/modify skill
        # reports multiple file matches, stage the choice so the pending-drive
        # gate can re-dispatch the ORIGINAL intent against the picked file.
        if (result.success and (result.data or {}).get("type") == "drive_file_choice"
                and phone):
            data = result.data
            update_user_context(phone, "pending_drive", {
                "step": "awaiting_file_choice",
                "intent": data.get("intent", "read"),
                "candidates": data.get("candidates", []),
                "edit_spec": data.get("edit_spec"),
            })

        return AgentResult(
            agent_name=self.agent_name,
            success=result.success,
            data=result.data,
            error_message=result.error_message,
        )
