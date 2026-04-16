import random

from app.agents.base_agent import BaseAgent
from app.models.agent_result import AgentResult
from app.models.parsed_message import ParsedMessage

GREETING_KEYWORDS = {"hola", "hello", "hey", "buenos días", "buenos dias",
                     "good morning", "buenas tardes", "good afternoon",
                     "buenas noches", "good evening", "buenas", "que tal", "qué tal"}
GRATITUDE_KEYWORDS = {"gracias", "thanks", "thank you", "thankss", "thanx", "grax", "tks"}

_GREETING_RESPONSES = {
    "es": [
        "¡Hola {name}! ¿En qué te puedo ayudar? 🐙",
        "¡Hey {name}! Aquí estoy, ¿qué necesitas? 🐙",
        "¡Hola {name}! Listo para ayudarte 🐙",
        "¡Qué tal {name}! ¿Cómo te ayudo hoy? 🐙",
    ],
    "en": [
        "Hey {name}! How can I help? 🐙",
        "Hi {name}! What do you need? 🐙",
        "Hello {name}! What can I do for you? 🐙",
        "Hey {name}! I'm here, what's up? 🐙",
    ],
}

_GRATITUDE_RESPONSES = {
    "es": [
        "¡Siempre a la orden! 🐙",
        "¡Con gusto! ¿Algo más? 🐙",
        "¡Para eso estoy! 🐙",
    ],
    "en": [
        "Anytime! 🐙",
        "You got it! Anything else? 🐙",
        "Happy to help! 🐙",
    ],
}


class GreetingAgent(BaseAgent):

    def execute(self, parsed: ParsedMessage, user: dict) -> AgentResult:
        signals = set(s.lower() for s in (parsed.signals or []))
        lang = (user.get("language") or "es").lower()
        name = user.get("name", "")

        if signals & GRATITUDE_KEYWORDS:
            msg_type = "gratitude"
            pool = _GRATITUDE_RESPONSES.get(lang, _GRATITUDE_RESPONSES["es"])
            response = random.choice(pool)
        else:
            msg_type = "greeting"
            pool = _GREETING_RESPONSES.get(lang, _GREETING_RESPONSES["es"])
            response = random.choice(pool).format(name=name)

        return AgentResult(
            agent_name="GreetingAgent",
            success=True,
            data={"type": msg_type, "response": response},
        )
