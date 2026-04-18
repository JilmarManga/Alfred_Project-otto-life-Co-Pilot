import logging

from app.agents.base_agent import BaseAgent
from app.models.agent_result import AgentResult
from app.models.parsed_message import ParsedMessage
from app.repositories.unknown_message_repository import UnknownMessageRepository

logger = logging.getLogger(__name__)

# Phrases that signal "the user is asking Otto to DO something we don't support yet."
# Scanned as lowercase substrings. Conservative — strong action-request signals only.
_CAPABILITY_REQUEST_PHRASES = {
    # Spanish
    "puedes ", "podrias ", "podrías ",
    "me ayudas", "ayudame", "ayúdame", "ayudarme",
    "necesito que", "quiero que", "quisiera que",
    "me puedes", "hazme ", "házmelo", "hazmelo",
    "cómprame", "comprame", "pídeme", "pideme",
    "resérvame", "reservame", "mándame", "mandame",
    "llámame", "llamame", "búscame", "buscame",
    "envíame", "enviame", "escríbeme", "escribeme",
    # English
    "can you ", "could you ", "would you ",
    "help me", "help with",
    "i need you to", "i want you to", "i'd like you to",
    "book me", "order me", "buy me", "get me",
    "send me", "call me", "find me", "remind me to",
}


def _is_capability_request(raw_message: str) -> bool:
    if not raw_message:
        return False
    text = raw_message.lower()
    return any(phrase in text for phrase in _CAPABILITY_REQUEST_PHRASES)


class AmbiguityAgent(BaseAgent):

    def execute(self, parsed: ParsedMessage, user: dict) -> AgentResult:
        phone = user.get("phone_number", "")
        raw = parsed.raw_message or ""
        is_capability = _is_capability_request(raw)
        category = "capability_request" if is_capability else "ambiguity"

        try:
            UnknownMessageRepository.log(
                user_phone_number=phone,
                raw_message=raw,
                category=category,
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

        data: dict = {"raw_message": raw, "signals": parsed.signals}
        if is_capability:
            data["type"] = "out_of_scope_request"

        return AgentResult(
            agent_name="AmbiguityAgent",
            success=True,
            data=data,
        )
