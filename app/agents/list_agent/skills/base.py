from abc import ABC, abstractmethod

from app.agents.list_agent.skill_context import SkillContext, SkillResult


class ListSkill(ABC):
    """Base class for all ListAgent skills.

    Rules (mirrored from TravelSkill):
    - No LLM calls.
    - No WhatsApp calls (no send_whatsapp_message).
    - No user-facing string composition.
    - No routing decisions.
    - Firestore reads/writes via ListRepository are allowed.
    - Return SkillResult. Never AgentResult — the Agent wraps that.
    """
    name: str  # class-level constant, e.g. "save_to_list"

    @abstractmethod
    def execute(self, ctx: SkillContext) -> SkillResult:
        ...
