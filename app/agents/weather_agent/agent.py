import logging

from app.agents.base_agent import BaseAgent
from app.agents.weather_agent.skill_context import SkillContext, SkillResult
from app.agents.weather_agent.skills.current_conditions import CurrentConditionsSkill
from app.agents.weather_agent.skills.rain_check import RainCheckSkill
from app.models.agent_result import AgentResult
from app.models.parsed_message import ParsedMessage

logger = logging.getLogger(__name__)

# Rain-related signals that trigger RainCheckSkill instead of CurrentConditionsSkill.
# These are a subset of WEATHER_KEYWORDS — already guaranteed to land on WeatherAgent.
_RAIN_SIGNALS = {"llover", "lloverá", "llueve", "lluvia", "rain", "raining"}


class WeatherAgent(BaseAgent):
    """Domain agent for weather queries.

    Public contract (unchanged from the old flat file):
        execute(parsed: ParsedMessage, user: dict) -> AgentResult

    Skills:
        current_conditions — general weather query (temp + description + rain %)
        rain_check         — rain-specific query (leads with precipitation probability)
    """

    agent_name = "WeatherAgent"

    _SKILLS = {
        "current_conditions": CurrentConditionsSkill,
        "rain_check": RainCheckSkill,
    }

    def execute(self, parsed: ParsedMessage, user: dict) -> AgentResult:
        skill_name = self._pick_skill(parsed)
        ctx = SkillContext(
            user=user,
            parsed=parsed,
            inbound_text=parsed.raw_message or "",
        )
        return self._run(skill_name, ctx)

    def run_skill(self, skill_name: str, ctx: SkillContext) -> AgentResult:
        return self._run(skill_name, ctx)

    def _pick_skill(self, parsed: ParsedMessage) -> str:
        signals = set(parsed.signals)
        if signals & _RAIN_SIGNALS:
            return "rain_check"
        return "current_conditions"

    def _run(self, skill_name: str, ctx: SkillContext) -> AgentResult:
        skill_cls = self._SKILLS.get(skill_name)
        if skill_cls is None:
            logger.error("WeatherAgent: unknown skill %r", skill_name)
            return AgentResult(
                agent_name=self.agent_name,
                success=False,
                error_message=f"unknown_skill:{skill_name}",
            )
        try:
            result: SkillResult = skill_cls().execute(ctx)
        except Exception as exc:
            logger.exception("WeatherAgent skill %r raised: %s", skill_name, exc)
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
