from abc import ABC, abstractmethod

from app.agents.weather_agent.skill_context import SkillContext, SkillResult


class WeatherSkill(ABC):
    """Base class for all WeatherAgent skills.

    Rules:
    - No LLM calls.
    - No WhatsApp calls.
    - No user-facing string composition.
    - No routing decisions.
    - External weather APIs are allowed.
    - Return SkillResult. Never AgentResult.
    """
    name: str

    @abstractmethod
    def execute(self, ctx: SkillContext) -> SkillResult:
        ...
