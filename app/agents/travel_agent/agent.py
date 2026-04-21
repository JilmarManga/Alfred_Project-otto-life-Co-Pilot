import logging

from app.agents.base_agent import BaseAgent
from app.agents.travel_agent.skill_context import SkillContext, SkillResult
from app.agents.travel_agent.skills.next_event_travel import NextEventTravelSkill
from app.models.agent_result import AgentResult
from app.models.parsed_message import ParsedMessage
from app.services.token_crypto import decrypt

logger = logging.getLogger(__name__)


class TravelAgent(BaseAgent):
    """Domain agent for travel-related queries.

    Public contract (unchanged from the old flat file):
        execute(parsed: ParsedMessage, user: dict) -> AgentResult

    Internal dispatch:
        _pick_skill_from_router -> skill name (str)
        run_skill(name, ctx)    -> AgentResult   [called by gates that bypass Layer 2]
        _run(name, ctx)         -> AgentResult   [shared execution path]

    Skills are registered in _SKILLS. Each skill receives a SkillContext and
    returns a SkillResult; this class wraps it into AgentResult(agent_name="TravelAgent").
    """

    agent_name = "TravelAgent"

    _SKILLS = {
        "next_event_travel": NextEventTravelSkill,
        # Phase 1 skills added in subsequent commits:
        # "resolve_event_location": ResolveEventLocationSkill,
        # "schedule_departure_reminder": ScheduleDepartureReminderSkill,
    }

    # ------------------------------------------------------------------ #
    # Public entry points                                                  #
    # ------------------------------------------------------------------ #

    def execute(self, parsed: ParsedMessage, user: dict) -> AgentResult:
        """Layer 2 (router) entry. Decrypts the calendar token, picks a skill,
        and executes it via the shared _run path."""
        phone = user.get("phone_number", "")

        encrypted = user.get("google_calendar_refresh_token")
        if not encrypted:
            return AgentResult(
                agent_name=self.agent_name,
                success=False,
                error_message="calendar_not_connected",
            )
        try:
            refresh_token = decrypt(encrypted)
        except Exception as exc:
            logger.exception("TravelAgent: token decrypt failed for %s: %s", phone, exc)
            return AgentResult(
                agent_name=self.agent_name,
                success=False,
                error_message="calendar_not_connected",
            )

        skill_name = self._pick_skill_from_router(parsed, user)
        ctx = SkillContext(
            user=user,
            parsed=parsed,
            inbound_text=parsed.raw_message,
            payload={"refresh_token": refresh_token},
        )
        return self._run(skill_name, ctx)

    def run_skill(self, skill_name: str, ctx: SkillContext) -> AgentResult:
        """Gate entry. Handlers that already know which skill they want call this
        directly, bypassing Layer 1 (parse) and Layer 2 (route)."""
        return self._run(skill_name, ctx)

    # ------------------------------------------------------------------ #
    # Internal helpers                                                     #
    # ------------------------------------------------------------------ #

    def _pick_skill_from_router(self, parsed: ParsedMessage, user: dict) -> str:
        """Deterministic skill selection from router context.
        Phase 1: all router-sourced calls go to next_event_travel.
        Future phases will inspect parsed.signals / user state here."""
        return "next_event_travel"

    def _run(self, skill_name: str, ctx: SkillContext) -> AgentResult:
        skill_cls = self._SKILLS.get(skill_name)
        if skill_cls is None:
            logger.error("TravelAgent: unknown skill %r", skill_name)
            return AgentResult(
                agent_name=self.agent_name,
                success=False,
                error_message=f"unknown_skill:{skill_name}",
            )
        try:
            result: SkillResult = skill_cls().execute(ctx)
        except Exception as exc:
            logger.exception("TravelAgent skill %r raised: %s", skill_name, exc)
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
