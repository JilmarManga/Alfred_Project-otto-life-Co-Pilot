from abc import ABC, abstractmethod

from app.agents.reminder_agent.skill_context import SkillContext, SkillResult


class ReminderSkill(ABC):
    """Base class for all ReminderAgent skills.

    Rules (mirrored from ListSkill / TravelSkill):
    - No LLM calls.
    - No WhatsApp calls (no send_whatsapp_message).
    - No user-facing string composition.
    - No routing decisions.
    - Firestore reads/writes via UserReminderRepository are allowed.
    - Return SkillResult. Never AgentResult — the Agent wraps that.
    """
    name: str  # class-level constant, e.g. "set_reminder"

    @abstractmethod
    def execute(self, ctx: SkillContext) -> SkillResult:
        ...
