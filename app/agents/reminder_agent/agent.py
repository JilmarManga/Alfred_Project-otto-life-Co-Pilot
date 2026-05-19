import logging
from typing import Optional

from app.agents.base_agent import BaseAgent
from app.agents.reminder_agent.skill_context import SkillContext, SkillResult
from app.agents.reminder_agent.skills.cancel_reminder import CancelReminderSkill
from app.agents.reminder_agent.skills.list_reminders import ListRemindersSkill
from app.agents.reminder_agent.skills.reschedule_reminder import RescheduleReminderSkill
from app.agents.reminder_agent.skills.set_reminder import SetReminderSkill
from app.models.agent_result import AgentResult
from app.models.parsed_message import ParsedMessage
# Imported from the parser (not the router) to avoid a circular import — the
# router imports ReminderAgent. These are the single source of the keyword sets.
from app.parser.message_parser import (
    CALENDAR_KEYWORDS,
    CREATE_KEYWORDS,
    REMINDER_OFF_KEYWORDS,
    REMINDER_ON_KEYWORDS,
)

logger = logging.getLogger(__name__)

# Pattern triggers used by `ReminderAgent.matches()`. Kept separate from the
# parser's `_scan_signals` keyword sets — reminders do NOT use the signals
# mechanism (same convention as ListAgent / DriveAgent).
_SET_TRIGGERS = {
    # Spanish
    "recuérdame", "recuerdame", "recuérdamelo", "recuerdamelo",
    "recordarme", "acuérdame", "acuerdame",
    "no me dejes olvidar", "no olvides recordarme",
    "ponme un recordatorio", "pon un recordatorio",
    "créame un recordatorio", "creame un recordatorio",
    # English
    "remind me", "don't let me forget", "dont let me forget",
    "set a reminder to", "set a reminder", "create a reminder",
}
_LIST_TRIGGERS = {
    # Spanish
    "qué recordatorios", "que recordatorios", "mis recordatorios",
    "cuáles recordatorios", "cuales recordatorios",
    # English
    "what reminders", "my reminders", "list my reminders",
    "which reminders",
}
_CANCEL_TRIGGERS = {
    # Spanish
    "cancela el recordatorio", "cancela recordatorio",
    "elimina el recordatorio", "borra el recordatorio",
    "quita el recordatorio",
    # English
    "cancel the reminder", "delete the reminder", "remove the reminder",
    "cancel my reminder",
}
_TOGGLE_GUARD = REMINDER_OFF_KEYWORDS | REMINDER_ON_KEYWORDS


class ReminderAgent(BaseAgent):
    """Domain agent for personal reminders (set / list / cancel).

    Public contract (matches every other agent):
        execute(parsed: ParsedMessage, user: dict) -> AgentResult

    Extra public classmethod:
        matches(parsed: ParsedMessage) -> bool   (router pattern predicate)

    Internal dispatch mirrors ListAgent:
        _pick_skill_from_router -> skill name (str | None)
        run_skill(name, ctx)    -> AgentResult   [gate bypass entry]
        _run(name, ctx)         -> AgentResult   [shared execution path]
    """

    agent_name = "ReminderAgent"

    _SKILLS = {
        "set_reminder":        SetReminderSkill,
        "list_reminders":      ListRemindersSkill,
        "cancel_reminder":     CancelReminderSkill,
        "reschedule_reminder": RescheduleReminderSkill,  # gate-only
    }

    # ------------------------------------------------------------------ #
    # Public entry points                                                  #
    # ------------------------------------------------------------------ #

    @classmethod
    def matches(cls, parsed: ParsedMessage) -> bool:
        """Deterministic pattern check: is this a personal-reminder op?"""
        if parsed.reminder_intent in {"set", "list", "cancel"}:
            return True
        text = (parsed.raw_message or "").lower().strip()
        if not text:
            return False
        # Never claim the calendar reminders on/off SETTING phrases.
        if any(p in text for p in _TOGGLE_GUARD):
            return False
        if any(kw in text for kw in _SET_TRIGGERS):
            return True
        if any(kw in text for kw in _LIST_TRIGGERS):
            return True
        if any(kw in text for kw in _CANCEL_TRIGGERS):
            return True
        return False

    def execute(self, parsed: ParsedMessage, user: dict) -> AgentResult:
        skill_name = self._pick_skill_from_router(parsed, user)
        if skill_name is None:
            return AgentResult(
                agent_name=self.agent_name,
                success=False,
                error_message="reminder_missing_text",
            )
        payload = {}
        if skill_name == "set_reminder" and self._is_reminder_or_event_ambiguous(parsed):
            payload["ambiguous"] = True
        ctx = SkillContext(
            user=user,
            parsed=parsed,
            inbound_text=parsed.raw_message,
            payload=payload,
        )
        return self._run(skill_name, ctx)

    def run_skill(self, skill_name: str, ctx: SkillContext) -> AgentResult:
        """Gate entry — handlers that already know the skill call this."""
        return self._run(skill_name, ctx)

    # ------------------------------------------------------------------ #
    # Internal helpers                                                     #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _has_set_trigger(parsed: ParsedMessage) -> bool:
        text = (parsed.raw_message or "").lower()
        return any(kw in text for kw in _SET_TRIGGERS)

    @classmethod
    def _is_reminder_or_event_ambiguous(cls, parsed: ParsedMessage) -> bool:
        """True when the message looks like a reminder AND a calendar event —
        ReminderAgent then stages its own clarify gate (never the router-level
        Disambiguation, which is coupled to ListAgent)."""
        reminder_signal = (parsed.reminder_intent == "set") or cls._has_set_trigger(parsed)
        if not reminder_signal:
            return False
        event_shape = bool(parsed.event_title and parsed.event_start)
        calendar_signal = bool(set(parsed.signals) & (CALENDAR_KEYWORDS | CREATE_KEYWORDS))
        return event_shape or calendar_signal

    def _pick_skill_from_router(
        self, parsed: ParsedMessage, user: dict,
    ) -> Optional[str]:
        intent = parsed.reminder_intent
        if intent == "list":
            return "list_reminders"
        if intent == "cancel":
            return "cancel_reminder"
        if intent == "set":
            return "set_reminder" if parsed.reminder_text else None

        # No LLM-extracted intent — fall back to trigger scan.
        text = (parsed.raw_message or "").lower()
        if any(kw in text for kw in _LIST_TRIGGERS):
            return "list_reminders"
        if any(kw in text for kw in _CANCEL_TRIGGERS):
            return "cancel_reminder"
        if any(kw in text for kw in _SET_TRIGGERS):
            return "set_reminder" if parsed.reminder_text else None
        return None

    def _run(self, skill_name: str, ctx: SkillContext) -> AgentResult:
        skill_cls = self._SKILLS.get(skill_name)
        if skill_cls is None:
            logger.error("ReminderAgent: unknown skill %r", skill_name)
            return AgentResult(
                agent_name=self.agent_name,
                success=False,
                error_message=f"unknown_skill:{skill_name}",
            )
        try:
            result: SkillResult = skill_cls().execute(ctx)
        except Exception as exc:
            logger.exception("ReminderAgent skill %r raised: %s", skill_name, exc)
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
