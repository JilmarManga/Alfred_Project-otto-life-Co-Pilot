import os
import logging
import openai
from app.models.agent_result import AgentResult

logger = logging.getLogger(__name__)
openai.api_key = os.getenv("OPENAI_API_KEY")
GPT_MODEL = "gpt-4o-mini"

FORMATTING_PROMPT = """You are Otto, a WhatsApp-native personal life co-pilot.

CRITICAL LANGUAGE RULE: You MUST respond in {lang_name} ONLY. This is non-negotiable.
Do not use any other language, even if the data you receive is in a different language.

General rules:
- Never return JSON, error codes, or technical language.
- Never sound like a bot.
- Use a variety of emojis naturally: 👍 👌 ✅ 🐙 💰 📝 🤙 ✓ 🫡

Agent-specific behavior:

ExpenseAgent:
- Respond with ONLY a single emoji or one word (e.g. 👍, Listo, Anotado, Got it, ✅).
- Never repeat the amount, currency, category, or any details back to the user.
- Maximum 2 words total. Vary it every time.

SummaryAgent:
- Give a concise expense summary.
- If totals contains multiple currencies, list each on its own line with thousands separators.
- Example: "💰 Esta semana:\n• COP $2.500.000\n• USD $150"

CalendarAgent:
- If type is "calendar_query": list all events clearly. Include time and location for each.
- If type is "calendar_followup": show ONLY that single event — its time and location. Nothing else.
- If type is "calendar_next_event": use this exact emoji-rich structure (one block, no extra text):
  🕐 [time] — [title]
  📍 [location]
  Sal a las [leave_at] — son [duration] min con tráfico 🚗   (Spanish) or  Leave by [leave_at] — [duration] min with traffic 🚗  (English)
  [weather emoji matching conditions] [temperature] [weather_summary]
  Always include the clock emoji matching the hour, 📍 for location, 🚗 for traffic, and a weather emoji (☀️🌤️⛅🌥️☁️🌧️⛈️🌩️❄️🌫️).

TravelAgent:
- Clearly state when the user should leave and how long the trip takes.
- Use natural phrasing like "Sal a las 8:20 — son 40 min con tráfico 🚗"

WeatherAgent:
- One line: temperature + description + an emoji matching the weather.
- If city_not_found is true: tell the user you couldn't find weather for that city name (use the city value from the data), and suggest they use the full city name. Example ES: "No encontré [city] 🌤️ Intenta con el nombre completo, ej: Cómo va a estar el clima hoy en San Francisco, CA". Example EN: "Couldn't find [city] 🌤️ Try the full name, e.g. How's the weather today in San Francisco, CA"

AmbiguityAgent:
- The user's message was unclear. Respond with a warm greeting and ONE natural clarifying question.
- Examples: "Hola! ¿En qué te puedo ayudar? 🐙" / "Hey! What can I help you with? 🐙"
- Never respond with just an emoji.

You will receive a structured result from the assistant's internal system.
Turn it into a warm, human WhatsApp message. No preamble. Respond in {lang_name} ONLY."""

# Fallbacks used when: (a) LLM formatting fails on a successful result, or (b) agent succeeded but edge case
_FALLBACKS = {
    "ExpenseAgent":  {"es": "👍 Anotado.", "en": "👍 Saved."},
    "SummaryAgent":  {"es": "Aquí va tu resumen.", "en": "Here's your summary."},
    "CalendarAgent": {"es": "Revisé tu agenda.", "en": "Checked your calendar."},
    "TravelAgent":   {"es": "Te digo cuándo salir.", "en": "I'll tell you when to leave."},
    "WeatherAgent":  {"es": "Aquí el clima.", "en": "Here's the weather."},
    "AmbiguityAgent":{"es": "¿En qué te puedo ayudar? 🐙", "en": "What can I help you with? 🐙"},
    "GreetingAgent": {"es": "¡Hola! ¿En qué te puedo ayudar? 🐙", "en": "Hey! How can I help? 🐙"},
}

_NEEDS_CURRENCY = {
    "es": "👌 Anotado. ¿En qué moneda fue? (COP, USD o EUR)",
    "en": "👌 Got it. Which currency was that? (COP, USD, or EUR)",
}

# Separate error messages for when the agent itself failed (expense not saved, etc.)
_ERROR_MESSAGES = {
    "ExpenseAgent":  {"es": "No pude guardar ese gasto. Intenta de nuevo 🙏", "en": "Couldn't save that expense. Try again 🙏"},
    "SummaryAgent":  {"es": "No pude obtener tu resumen. Intenta de nuevo 🙏", "en": "Couldn't get your summary. Try again 🙏"},
    "CalendarAgent": {"es": "No pude acceder a tu agenda. Intenta de nuevo 🙏", "en": "Couldn't access your calendar. Try again 🙏"},
    "TravelAgent":   {"es": "No pude calcular el viaje. Intenta de nuevo 🙏", "en": "Couldn't calculate travel time. Try again 🙏"},
    "WeatherAgent":  {"es": "No pude obtener el clima. Intenta de nuevo 🙏", "en": "Couldn't get the weather. Try again 🙏"},
    "GreetingAgent": {"es": "¡Hola! 🐙", "en": "Hey! 🐙"},
}


def format_response(result: AgentResult, user: dict) -> str:
    """
    Layer 4: Convert AgentResult → warm WhatsApp message string.
    Calls LLM with user's language; falls back to templates on failure.
    """
    lang = user.get("language", "es")
    lang_name = "Spanish" if lang == "es" else "English"
    name = user.get("name", "")
    agent = result.agent_name

    # Greeting/gratitude — hardcoded responses, no LLM call needed.
    if agent == "GreetingAgent" and result.success:
        return result.data.get("response", _FALLBACKS.get("GreetingAgent", {}).get(lang, "🐙"))

    # Special case: expense needs a currency answer — not a real error.
    if agent == "ExpenseAgent" and result.data.get("needs_currency"):
        return _NEEDS_CURRENCY.get(lang, _NEEDS_CURRENCY["en"])

    # Agent failed — use distinct error message (not the success fallback)
    if not result.success:
        return _ERROR_MESSAGES.get(agent, {}).get(lang, "Algo salió mal. Intenta de nuevo 🙏" if lang == "es" else "Something went wrong. Try again 🙏")

    try:
        prompt = FORMATTING_PROMPT.format(lang_name=lang_name)

        user_content = (
            f"User name: {name}\n"
            f"Agent: {agent}\n"
            f"IMPORTANT: Respond in {lang_name} only.\n"
            f"Result: {result.data}"
        )

        response = openai.chat.completions.create(
            model=GPT_MODEL,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": user_content},
            ],
            temperature=0.7,
        )
        content = response.choices[0].message.content
        if not content:
            raise ValueError("Empty LLM response")
        return content.strip()

    except Exception as e:
        logger.warning("Response formatter LLM failed: %s", e)
        return _FALLBACKS.get(agent, {}).get(lang, "👍")
