import os
import logging
import random
from datetime import datetime

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
- Use a variety of emojis naturally: 👍 👌 ✅ 🐙 💰 📝 🤙 ✓ 🫡 🙂 😊

Agent-specific behavior:

ExpenseAgent:
- Respond with a short confirmation. Almost always (most of the time), use either a single word, or a single emoji + 1–2 words.
  Examples: "Anotado 👍", "Listo", "Got it ✅", "Guardado ✅", "Saved", "Done 👍", "Listo 👌", "Got it".
- Occasionally, use a short warm phrase instead.
  Examples: "Listo, lo anoté 🙂", "Perfecto, lo guardé ✅", "Dale, lo tengo 🫡", "Ya quedó 👌", "Got it, saved 🙂".
- Never repeat the amount, currency, or category of the just-saved expense.
- Maximum ~4 words. Vary it every time — don't repeat the same phrase back-to-back.

SummaryAgent:
- Give a concise expense summary.
- If totals contains multiple currencies, list each on its own line with thousands separators.
- Example: "💰 Esta semana:\n• COP $2.500.000\n• USD $150"

CalendarAgent:
- If type is "calendar_query": list all events clearly. For each event show time, title, and location (if location is missing or null, render exactly "📍 No location" — do NOT translate it). DO NOT include travel plan, leave_at, duration, or traffic info for any event in this list — travel only appears in calendar_next_event and calendar_followup.
- If type is "calendar_followup": show ONLY that single event. Structure:
  🕐 [time] — [title]
  📍 [location]  (if location is missing or null, render exactly "📍 No location" — do NOT translate it)
  IF leave_at AND duration_minutes are BOTH present in the data: render "Sal a las [leave_at] — son [duration] min con tráfico 🚗" (Spanish) or "Leave by [leave_at] — [duration] min with traffic 🚗" (English).
  ELSE: render "Dime la ubicación y te ayudo con el plan de viaje 🗺️" (Spanish) or "Tell me the location and I'll help you plan the commute 🗺️" (English).
  Nothing else.
- If type is "calendar_next_event": use this exact emoji-rich structure (one block, no extra text):
  🕐 [time] — [title]
  📍 [location]  (if location is missing or null, render exactly "📍 No location" — do NOT translate it)
  IF leave_at AND duration_minutes are BOTH present in the data: render "Sal a las [leave_at] — son [duration] min con tráfico 🚗" (Spanish) or "Leave by [leave_at] — [duration] min with traffic 🚗" (English).
  ELSE: render "Dime la ubicación y te ayudo con el plan de viaje 🗺️" (Spanish) or "Tell me the location and I'll help you plan the commute 🗺️" (English).
  [weather emoji matching conditions] [temperature] [weather_summary]
  Always include the clock emoji matching the hour and a weather emoji (☀️🌤️⛅🌥️☁️🌧️⛈️🌩️❄️🌫️).
  NEVER invent leave_at, duration_minutes, or travel timing values — only use what is explicitly in the data.
- If type is "calendar_create": ONE line only — confirmation + title + day + time + 📍 location (if present).
  Example ES: "Guardado ✅ Almuerzo con amigos — mié 2pm 📍 CC Titan Plaza"
  Example EN: "Saved ✅ Lunch with friends — Wed 2pm 📍 CC Titan Plaza"
  Use a short weekday abbreviation. Never add extra text — the follow-up question is sent as a separate message.

TravelAgent:
- If type is "travel_leave_plan": one line with leave time and duration, then naturally ask if they want a departure reminder.
  Example ES: "Sal a las [leave_at] — son [duration_minutes] min con tráfico 🚗 ¿Te aviso cuando sea hora de salir?"
  Example EN: "Leave by [leave_at] — [duration_minutes] min with traffic 🚗 Want me to remind you when it's time to leave?"
  Never use curly-brace variables — only square-bracket placeholders as shown above.
- Otherwise: clearly state when the user should leave and how long the trip takes.
  Use natural phrasing like "Sal a las 8:20 — son 40 min con tráfico 🚗"

WeatherAgent:
- If city_not_found is true: tell the user you couldn't find weather for that city name (use the city value from the data), and suggest they use the full city name. Example ES: "No encontré [city] 🌤️ Intenta con el nombre completo, ej: Cómo va a estar el clima hoy en San Francisco, CA". Example EN: "Couldn't find [city] 🌤️ Try the full name, e.g. How's the weather today in San Francisco, CA"
- If forecast_unavailable is true: be honest. Acknowledge you couldn't get the forecast and share the current data you do have. One line. Example ES: "No pude obtener el pronóstico ahorita 🌤️ Pero ahora mismo está [temperature] con [summary]". Example EN: "Couldn't get the forecast right now 🌤️ But right now it's [temperature], [summary]"
- If type is "weather_rain_check" and forecast_unavailable is false: lead with rain probability. Three levels based on rain_probability_pct: >=60 high chance, 30-59 might rain, <30 unlikely. Examples ES: "🌧️ Sí, hay un [rain_probability_pct]% de probabilidad de lluvia hoy. [temperature], [summary]" / "🌦️ Puede que llueva, [rain_probability_pct]% de probabilidad. [temperature], [summary]" / "☀️ No parece que llueva hoy ([rain_probability_pct]%). [temperature], [summary]". Same logic in English.
- If type is "weather_general" and forecast_unavailable is false: one line with temperature + description + emoji + rain probability appended. Example ES: "🌤️ [temperature], [summary]. [rain_probability_pct]% de probabilidad de lluvia hoy". Example EN: "🌤️ [temperature], [summary]. [rain_probability_pct]% chance of rain today"

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

# Error codes that beat the generic agent-level error message above.
# Checked via result.error_message (exact match).
_SPECIFIC_ERRORS = {
    "missing_event_details": {
        "es": "No entendí los detalles del evento. Dime el título y la hora 🙏",
        "en": "I didn't catch the event details. Tell me the title and time 🙏",
    },
    "create_failed": {
        "es": "No pude crear el evento. Intenta de nuevo 🙏",
        "en": "Couldn't create the event. Try again 🙏",
    },
    "reminder_toggle_failed": {
        "es": "No pude actualizar tus recordatorios. Intenta de nuevo 🙏",
        "en": "Couldn't update your reminders. Try again 🙏",
    },
    # TravelAgent — location resolution errors
    "geocode_not_found": {
        "es": "No encontré ese lugar 🗺️ ¿Puedes darme el nombre completo o la dirección exacta?",
        "en": "Couldn't find that place 🗺️ Can you give me the full name or exact address?",
    },
    "geocode_ambiguous": {
        "es": "Ese nombre puede ser varios lugares 🗺️ ¿Puedes ser más específico? Por ejemplo: Bogotá, Colombia o el nombre completo del lugar.",
        "en": "That name matches several places 🗺️ Can you be more specific? For example: the full city name or address.",
    },
    "maps_unavailable_for_place": {
        "es": "No pude calcular el tiempo de viaje ahora. Intenta de nuevo en un momento 🙏",
        "en": "Couldn't calculate travel time right now. Try again in a moment 🙏",
    },
    "no_upcoming_event_for_location": {
        "es": "No encontré un evento próximo al que asociar esa ubicación 📅",
        "en": "I couldn't find an upcoming event to match that location to 📅",
    },
    "reminder_save_failed": {
        "es": "No pude guardar el recordatorio. Intenta de nuevo 🙏",
        "en": "Couldn't save the reminder. Try again 🙏",
    },
    "reminder_data_incomplete": {
        "es": "No pude crear el recordatorio — faltan datos del evento 🙏",
        "en": "Couldn't create the reminder — some event data was missing 🙏",
    },
}

# Per-type success fallbacks used when the LLM formatting call fails for a
# specific data.type where the agent-level fallback ("Revisé tu agenda")
# would be misleading (e.g. on creation success).
_TYPE_FALLBACKS = {
    "calendar_create": {"es": "Guardado ✅", "en": "Saved ✅"},
    "travel_leave_plan": {
        "es": "Te digo cuándo salir 🚗 ¿Quieres que te avise?",
        "en": "I'll tell you when to leave 🚗 Want me to remind you?",
    },
}

# Hardcoded copy for travel reminder confirmation and abort (no LLM needed).
_TRAVEL_REMINDER_CONFIRMED_COPY = {
    "es": "Listo, te aviso cuando sea hora de salir 🔔",
    "en": "Got it, I'll ping you when it's time to leave 🔔",
}

_TRAVEL_REMINDER_ABORTED_COPY = {
    "es": "Entendido, no te mando recordatorio 🙂",
    "en": "Got it, no reminder then 🙂",
}

# Day-name tables for short-circuited messages that need a locale-aware weekday.
_DAY_NAMES = {
    "es": ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"],
    "en": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
}

_CLARIFY_TEMPLATES = {
    "es": "Solo para confirmar: ¿quieres que agende '{title}' para {time}, o solo verificar si ya lo tienes? 🐙",
    "en": "Just to confirm: do you want me to create '{title}' for {time}, or just check if it's on your calendar? 🐙",
}

_REMINDER_OPT_OUT_COPY = {
    "es": "Listo, ya no recibirás recordatorios 🙂",
    "en": "Done, no more reminders for you 🙂",
}
_REMINDER_OPT_IN_COPY = {
    "es": "¡Listo! Vuelvo a enviarte recordatorios 🔔",
    "en": "Done! I'll send you reminders again 🔔",
}

_OUT_OF_SCOPE_COPY = {
    "es": [
        "Todavía no sé hacer eso 🐙 pero lo agrego a mi lista de habilidades por aprender. Te aviso cuando lo tenga listo para que me lo dejes a mí la próxima. ¿Hay algo más en lo que sí pueda ayudarte?",
        "Eso aún no está en mis superpoderes 🐙 pero lo anoto para aprenderlo. Te escribo apenas lo domine. ¿En qué más te puedo ayudar hoy?",
        "Uff, eso todavía no lo manejo 🐙 lo agrego a las próximas habilidades que voy a aprender. Te aviso cuando pueda. ¿Hay algo más que pueda hacer por ti?",
    ],
    "en": [
        "I can't do that yet 🐙 but I'm adding it to the skills I'll learn. I'll let you know when I've got it so you can leave it to me next time. Is there anything else I can help you with?",
        "That's not one of my skills yet 🐙 I'm noting it down to learn. I'll message you the moment I can handle it. Anything else I can help with today?",
        "Not in my toolbox yet 🐙 but I'm adding it to what I'll learn next. I'll ping you once I can take it off your plate. Is there something else I can do for you?",
    ],
}


def _format_time_for_clarify(iso_start: str, lang: str) -> str:
    """Small, locale-aware time rendering for the clarify short-circuit.
    Returns strings like 'miércoles 14:00' (es) or 'Wednesday 2pm' (en)."""
    if not iso_start:
        return "esa hora" if lang == "es" else "that time"
    try:
        dt = datetime.fromisoformat(iso_start)
    except (ValueError, TypeError):
        return iso_start

    day = _DAY_NAMES.get(lang, _DAY_NAMES["es"])[dt.weekday()]
    if lang == "en":
        hour12 = dt.hour % 12 or 12
        ampm = "pm" if dt.hour >= 12 else "am"
        time_part = f"{hour12}:{dt.minute:02d}{ampm}" if dt.minute else f"{hour12}{ampm}"
    else:
        time_part = f"{dt.hour:02d}:{dt.minute:02d}"
    return f"{day} {time_part}"


def _build_clarify_message(data: dict, lang: str) -> str:
    title = (data or {}).get("title") or ("un evento" if lang == "es" else "an event")
    time_display = _format_time_for_clarify((data or {}).get("start"), lang)
    template = _CLARIFY_TEMPLATES.get(lang, _CLARIFY_TEMPLATES["es"])
    return template.format(title=title, time=time_display)


def format_response(result: AgentResult, user: dict) -> str:
    """
    Layer 4: Convert AgentResult → warm WhatsApp message string.
    Calls LLM with user's language; falls back to templates on failure.
    """
    lang = user.get("language", "es")
    lang_name = "Spanish" if lang == "es" else "English"
    name = user.get("name", "")
    agent = result.agent_name
    data = result.data or {}
    data_type = data.get("type")

    # Greeting/gratitude — hardcoded responses, no LLM call needed.
    if agent == "GreetingAgent" and result.success:
        return data.get("response", _FALLBACKS.get("GreetingAgent", {}).get(lang, "🐙"))

    # Calendar clarify — hardcoded, deterministic question. No LLM call.
    if agent == "CalendarAgent" and result.success and data_type == "calendar_clarify_create":
        return _build_clarify_message(data, lang)

    # Reminder toggle — hardcoded confirmation, no LLM call.
    if agent == "CalendarAgent" and result.success and data_type == "reminder_opt_out":
        return _REMINDER_OPT_OUT_COPY.get(lang, _REMINDER_OPT_OUT_COPY["es"])
    if agent == "CalendarAgent" and result.success and data_type == "reminder_opt_in":
        return _REMINDER_OPT_IN_COPY.get(lang, _REMINDER_OPT_IN_COPY["es"])

    # Travel reminder confirmed / aborted — hardcoded, no LLM call.
    if agent == "TravelAgent" and result.success and data_type == "travel_reminder_confirmed":
        return _TRAVEL_REMINDER_CONFIRMED_COPY.get(lang, _TRAVEL_REMINDER_CONFIRMED_COPY["es"])
    if agent == "TravelAgent" and result.success and data_type == "travel_reminder_aborted":
        return _TRAVEL_REMINDER_ABORTED_COPY.get(lang, _TRAVEL_REMINDER_ABORTED_COPY["es"])

    # Out-of-scope capability request — hardcoded warm response, no LLM call.
    # Tells the user we can't do this yet and that we're adding it to our backlog.
    if agent == "AmbiguityAgent" and result.success and data_type == "out_of_scope_request":
        variants = _OUT_OF_SCOPE_COPY.get(lang, _OUT_OF_SCOPE_COPY["en"])
        return random.choice(variants)

    # Special case: expense needs a currency answer — not a real error.
    if agent == "ExpenseAgent" and data.get("needs_currency"):
        return _NEEDS_CURRENCY.get(lang, _NEEDS_CURRENCY["en"])

    # Agent failed — use distinct error message (not the success fallback).
    # Specific error codes beat the generic agent-level message.
    if not result.success:
        if result.error_message and result.error_message in _SPECIFIC_ERRORS:
            return _SPECIFIC_ERRORS[result.error_message].get(lang, _SPECIFIC_ERRORS[result.error_message]["es"])
        return _ERROR_MESSAGES.get(agent, {}).get(lang, "Algo salió mal. Intenta de nuevo 🙏" if lang == "es" else "Something went wrong. Try again 🙏")

    try:
        prompt = FORMATTING_PROMPT.format(lang_name=lang_name)

        # Strip dispatch-only fields from the data shown to the LLM so it
        # doesn't accidentally include them in the rendered reply.
        data_for_llm = {k: v for k, v in data.items() if k != "follow_up_message"}

        user_content = (
            f"User name: {name}\n"
            f"Agent: {agent}\n"
            f"IMPORTANT: Respond in {lang_name} only.\n"
            f"Result: {data_for_llm}"
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
        if data_type in _TYPE_FALLBACKS:
            return _TYPE_FALLBACKS[data_type].get(lang, _TYPE_FALLBACKS[data_type]["es"])
        return _FALLBACKS.get(agent, {}).get(lang, "👍")
