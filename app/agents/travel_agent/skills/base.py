from abc import ABC, abstractmethod

from app.agents.travel_agent.skill_context import SkillContext, SkillResult


class TravelSkill(ABC):
    """Base class for all TravelAgent skills.

    Rules:
    - No LLM calls.
    - No WhatsApp calls (no send_whatsapp_message).
    - No user-facing string composition.
    - No routing decisions.
    - Maps / Calendar / Geocoding API calls are allowed (domain APIs).
    - Return SkillResult. Never AgentResult — the Agent wraps that.
    """
    name: str  # class-level constant, e.g. "next_event_travel"

    @abstractmethod
    def execute(self, ctx: SkillContext) -> SkillResult:
        ...
