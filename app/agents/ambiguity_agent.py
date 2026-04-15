import logging

from app.agents.base_agent import BaseAgent
from app.models.agent_result import AgentResult
from app.models.parsed_message import ParsedMessage
from app.repositories.unknown_message_repository import UnknownMessageRepository

logger = logging.getLogger(__name__)


class AmbiguityAgent(BaseAgent):

    def execute(self, parsed: ParsedMessage, user: dict) -> AgentResult:
        phone = user.get("phone_number", "")
        try:
            UnknownMessageRepository.log(
                user_phone_number=phone,
                raw_message=parsed.raw_message or "",
                category="ambiguity",
                language=user.get("language"),
                onboarding_state=user.get("onboarding_state"),
                parsed_signals=parsed.signals or [],
                routed_to="AmbiguityAgent",
                user_context={
                    "name": user.get("name"),
                    "location": user.get("location"),
                    "timezone": user.get("timezone"),
                },
            )
        except Exception as exc:
            logger.warning("Failed to log ambiguous message for %s: %s", phone, exc)

        return AgentResult(
            agent_name="AmbiguityAgent",
            success=True,
            data={
                "raw_message": parsed.raw_message,
                "signals": parsed.signals,
            },
        )
