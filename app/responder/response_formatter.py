import json
import logging
import os
import random
import re
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
    "ExpenseAgent":     {"es": "👍 Anotado.", "en": "👍 Saved."},
    "SummaryAgent":     {"es": "Aquí va tu resumen.", "en": "Here's your summary."},
    "CalendarAgent":    {"es": "Revisé tu agenda.", "en": "Checked your calendar."},
    "TravelAgent":      {"es": "Te digo cuándo salir.", "en": "I'll tell you when to leave."},
    "WeatherAgent":     {"es": "Aquí el clima.", "en": "Here's the weather."},
    "AmbiguityAgent":   {"es": "¿En qué te puedo ayudar? 🐙", "en": "What can I help you with? 🐙"},
    "GreetingAgent":    {"es": "¡Hola! ¿En qué te puedo ayudar? 🐙", "en": "Hey! How can I help? 🐙"},
    "TypeClarifyAgent": {"es": "¿Es una cita en tu agenda o un gasto? 🗓️", "en": "Is this a calendar appointment or an expense? 🗓️"},
}

_NEEDS_CURRENCY = {
    "es": "👌 Anotado. ¿En qué moneda fue? (COP, USD o EUR)",
    "en": "👌 Got it. Which currency was that? (COP, USD, or EUR)",
}

_TYPE_CLARIFY_COPY = {
    "es": "¿'{title}' es una cita en tu agenda o un gasto? 🗓️",
    "en": "Is '{title}' a calendar appointment or an expense? 🗓️",
    "es_no_title": "¿Es una cita en tu agenda o un gasto? 🗓️",
    "en_no_title": "Is this a calendar appointment or an expense? 🗓️",
}

# Separate error messages for when the agent itself failed (expense not saved, etc.)
_ERROR_MESSAGES = {
    "ExpenseAgent":  {"es": "No pude guardar ese gasto. Intenta de nuevo 🙏", "en": "Couldn't save that expense. Try again 🙏"},
    "SummaryAgent":  {"es": "No pude obtener tu resumen. Intenta de nuevo 🙏", "en": "Couldn't get your summary. Try again 🙏"},
    "CalendarAgent": {"es": "No pude acceder a tu agenda. Intenta de nuevo 🙏", "en": "Couldn't access your calendar. Try again 🙏"},
    "TravelAgent":   {"es": "No pude calcular el viaje. Intenta de nuevo 🙏", "en": "Couldn't calculate travel time. Try again 🙏"},
    "WeatherAgent":  {"es": "No pude obtener el clima. Intenta de nuevo 🙏", "en": "Couldn't get the weather. Try again 🙏"},
    "GreetingAgent": {"es": "¡Hola! 🐙", "en": "Hey! 🐙"},
    "ListAgent":     {"es": "No pude procesar tu lista. Intenta de nuevo 🙏", "en": "Couldn't process your list. Try again 🙏"},
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
    # ListAgent — static failure copy (data-driven list errors are handled
    # by dedicated branches in format_response: list_not_found, list_cap_reached,
    # empty_list).
    "save_failed": {
        "es": "No pude guardar eso en tu lista. Intenta de nuevo 🙏",
        "en": "Couldn't save that to your list. Try again 🙏",
    },
    "delete_failed": {
        "es": "No pude eliminar la lista. Intenta de nuevo 🙏",
        "en": "Couldn't delete the list. Try again 🙏",
    },
    "missing_item": {
        "es": "No entendí qué quieres guardar 🤔 Mándamelo otra vez.",
        "en": "I couldn't tell what to save 🤔 Send it again?",
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


# ---- ListAgent hardcoded copy -------------------------------------------- #
# Every rendering below uses Python .format() — no LLM is called from these
# dicts, so {variable} placeholders are safe here. Hard Rule #11 only applies
# inside FORMATTING_PROMPT (which is sent to the LLM).

_LIST_LABEL_SUFFIX = {
    "es": " — etiqueta: {label}",
    "en": " — label: {label}",
}

_LIST_SAVED_COPY = {
    "es": "Listo ✅ Guardé “{content}” en tu lista ‘{list_name}’{label_suffix}.",
    "en": "Got it ✅ Saved “{content}” to your ‘{list_name}’ list{label_suffix}.",
}

_LIST_SAVED_DEDUPED_COPY = {
    "es": "Ya lo tenías guardado en ‘{list_name}’ hace un momento — no lo añadí otra vez 👍",
    "en": "You saved that to ‘{list_name}’ a moment ago — I didn't add it again 👍",
}

_LIST_CHOICE_REQUEST_COPY = {
    "es": "¿En cuál lista lo guardo? Tienes: {names}.",
    "en": "Which list should I save it to? You have: {names}.",
}

_LIST_DELETE_CONFIRM_COPY = {
    "es": "¿Confirmas que quieres eliminar la lista ‘{list_name}’? {has_clause} y no se podrá recuperar.",
    "en": "Confirm — delete the list ‘{list_name}’? {has_clause} and can't be recovered.",
}
_LIST_HAS_CLAUSE = {
    "es": {"singular": "Tiene {n} item", "plural": "Tiene {n} items", "zero": "Está vacía"},
    "en": {"singular": "It has {n} item", "plural": "It has {n} items", "zero": "It's empty"},
}

_LIST_DELETED_COPY = {
    "es": "Listo, eliminé la lista ‘{list_name}’ 🗑️",
    "en": "Done — I deleted the list ‘{list_name}’ 🗑️",
}

_LIST_CAP_REACHED_COPY = {
    "es": "Ya tienes 3 listas ({names}). Borra una primero si quieres crear otra 📋",
    "en": "You already have 3 lists ({names}). Delete one first if you want to create another 📋",
}

_LIST_NOT_FOUND_NONE_COPY = {
    "es": "Todavía no tienes listas guardadas 📋 Dime qué guardar y te creo una.",
    "en": "You don't have any saved lists yet 📋 Tell me what to save and I'll start one.",
}
_LIST_NOT_FOUND_ASK_COPY = {
    "es": "¿Cuál lista quieres? Tienes: {names}.",
    "en": "Which list do you mean? You have: {names}.",
}
_LIST_NOT_FOUND_MISS_COPY = {
    "es": "No encontré una lista con ese nombre 🔎 Tienes: {names}.",
    "en": "I couldn't find a list with that name 🔎 You have: {names}.",
}

_LIST_EMPTY_COPY = {
    "es": "La lista ‘{list_name}’ está vacía 📭",
    "en": "Your ‘{list_name}’ list is empty 📭",
}

_LIST_RECALL_HEADER_COPY = {
    "es": "📋 {list_name}",
    "en": "📋 {list_name}",
}

_LIST_DISAMBIG_COPY = {
    "es": "No sé si quieres {action_a} o {action_b}. ¿Cuál?",
    "en": "Not sure if you want to {action_a} or {action_b}. Which one?",
}

# Agent class name → short human action phrase for list_disambiguation.
_DISAMBIG_ACTION_COPY = {
    "ListAgent":     {"es": "guardarlo en una lista",    "en": "save it to a list"},
    "ExpenseAgent":  {"es": "anotarlo como gasto",        "en": "log it as an expense"},
    "CalendarAgent": {"es": "agregarlo al calendario",    "en": "add it to your calendar"},
    "TravelAgent":   {"es": "calcular tiempo de viaje",   "en": "calculate travel time"},
    "WeatherAgent":  {"es": "consultar el clima",         "en": "check the weather"},
    "SummaryAgent":  {"es": "darte un resumen de gastos", "en": "get you an expense summary"},
}

_URL_RE = re.compile(r"^https?://", re.IGNORECASE)


def _is_url(text: str) -> bool:
    if not text:
        return False
    return bool(_URL_RE.match(text.strip()))


def _batch_describe_urls(urls: list, lang: str) -> list:
    """One batched LLM call to produce a 3–6 word description per URL based
    on domain/path only (no page fetch). Returns a list aligned with `urls`,
    or [] on any failure — recall rendering must not block on LLM errors."""
    if not urls:
        return []
    try:
        lang_name = "Spanish" if lang == "es" else "English"
        url_block = "\n".join(f"{i + 1}. {u}" for i, u in enumerate(urls))
        system_prompt = (
            f"You describe URLs in {lang_name}. For each URL, give a 3–6 word "
            f"description inferred from the domain and URL path only. Do NOT "
            f"attempt to fetch the page. Return ONLY a JSON array of strings, "
            f"one per URL, in the same order as provided. No preamble, no keys."
        )
        response = openai.chat.completions.create(
            model=GPT_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": url_block},
            ],
            temperature=0.0,
        )
        content = (response.choices[0].message.content or "").strip()
        if content.startswith("```"):
            content = re.sub(r"```[a-z]*", "", content).replace("```", "").strip()
        parsed = json.loads(content)
        if isinstance(parsed, list) and len(parsed) == len(urls):
            return [str(d).strip() for d in parsed]
    except Exception as exc:
        logger.warning("List recall URL describe failed: %s", exc)
    return []


def _has_clause(n: int, lang: str) -> str:
    bucket = _LIST_HAS_CLAUSE.get(lang, _LIST_HAS_CLAUSE["es"])
    if n == 0:
        return bucket["zero"]
    key = "singular" if n == 1 else "plural"
    return bucket[key].format(n=n)


def _format_names(names: list) -> str:
    return ", ".join(f"‘{n}’" for n in names if n)


def _render_list_recall(data: dict, lang: str) -> str:
    """Deterministic numbered render of a recalled list. For URL items we
    attempt one batched LLM describe call — fail-open: on any error, URLs
    are rendered alone without descriptions."""
    list_name = data.get("list_name") or ""
    items = list(data.get("items") or [])

    url_indices = [i for i, it in enumerate(items) if _is_url(it.get("content", ""))]
    descriptions = {}
    if url_indices:
        urls = [items[i].get("content", "") for i in url_indices]
        descs = _batch_describe_urls(urls, lang)
        if descs and len(descs) == len(url_indices):
            descriptions = dict(zip(url_indices, descs))

    lines = [_LIST_RECALL_HEADER_COPY.get(lang, _LIST_RECALL_HEADER_COPY["es"]).format(list_name=list_name)]
    for i, item in enumerate(items):
        content = (item.get("content") or "").strip()
        raw_label = item.get("label")
        label_prefix = f"{raw_label}: " if raw_label else ""
        if i in descriptions and descriptions[i]:
            lines.append(f"{i + 1}. {label_prefix}{descriptions[i]} — {content}")
        else:
            lines.append(f"{i + 1}. {label_prefix}{content}")
    return "\n".join(lines)


def _render_list_disambiguation(candidates: list, lang: str) -> str:
    """Show both candidate actions so the user can pick one."""
    if len(candidates) < 2:
        return _LIST_NOT_FOUND_ASK_COPY.get(lang, _LIST_NOT_FOUND_ASK_COPY["es"]).format(names="")
    action_a = _DISAMBIG_ACTION_COPY.get(candidates[0], {}).get(lang) or candidates[0]
    action_b = _DISAMBIG_ACTION_COPY.get(candidates[1], {}).get(lang) or candidates[1]
    return _LIST_DISAMBIG_COPY.get(lang, _LIST_DISAMBIG_COPY["es"]).format(
        action_a=action_a, action_b=action_b,
    )


def _render_list_not_found(data: dict, lang: str) -> str:
    """Pick the right list_not_found variant from the data context."""
    existing = [n for n in (data.get("existing_names") or []) if n]
    requested = data.get("requested_name")
    if not existing:
        return _LIST_NOT_FOUND_NONE_COPY.get(lang, _LIST_NOT_FOUND_NONE_COPY["es"])
    names = _format_names(existing)
    if requested:
        return _LIST_NOT_FOUND_MISS_COPY.get(lang, _LIST_NOT_FOUND_MISS_COPY["es"]).format(names=names)
    return _LIST_NOT_FOUND_ASK_COPY.get(lang, _LIST_NOT_FOUND_ASK_COPY["es"]).format(names=names)


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

    # Calendar-or-expense disambiguation — hardcoded question, no LLM call.
    if agent == "TypeClarifyAgent" and result.success and data_type == "expense_or_calendar_clarify":
        title = data.get("event_title") or ""
        if title:
            key = lang if lang in ("es", "en") else "es"
            return _TYPE_CLARIFY_COPY[key].format(title=title)
        return _TYPE_CLARIFY_COPY.get(f"{lang}_no_title", _TYPE_CLARIFY_COPY["es_no_title"])

    # Out-of-scope capability request — hardcoded warm response, no LLM call.
    # Tells the user we can't do this yet and that we're adding it to our backlog.
    if agent == "AmbiguityAgent" and result.success and data_type == "out_of_scope_request":
        variants = _OUT_OF_SCOPE_COPY.get(lang, _OUT_OF_SCOPE_COPY["en"])
        return random.choice(variants)

    # ListAgent success branches — every render below is deterministic
    # (no LLM call) except list_recall's optional URL-describe helper.
    if agent == "ListAgent" and result.success:
        if data_type == "list_saved":
            label_val = data.get("label")
            label_suffix = (
                _LIST_LABEL_SUFFIX.get(lang, _LIST_LABEL_SUFFIX["es"]).format(label=label_val)
                if label_val else ""
            )
            return _LIST_SAVED_COPY.get(lang, _LIST_SAVED_COPY["es"]).format(
                content=data.get("content_preview") or "",
                list_name=data.get("list_name") or "",
                label_suffix=label_suffix,
            )
        if data_type == "list_saved_deduped":
            return _LIST_SAVED_DEDUPED_COPY.get(lang, _LIST_SAVED_DEDUPED_COPY["es"]).format(
                list_name=data.get("list_name") or "",
            )
        if data_type == "list_choice_request":
            return _LIST_CHOICE_REQUEST_COPY.get(lang, _LIST_CHOICE_REQUEST_COPY["es"]).format(
                names=_format_names(data.get("list_names") or []),
            )
        if data_type == "list_delete_confirm":
            return _LIST_DELETE_CONFIRM_COPY.get(lang, _LIST_DELETE_CONFIRM_COPY["es"]).format(
                list_name=data.get("list_name") or "",
                has_clause=_has_clause(int(data.get("item_count") or 0), lang),
            )
        if data_type == "list_deleted":
            return _LIST_DELETED_COPY.get(lang, _LIST_DELETED_COPY["es"]).format(
                list_name=data.get("list_name") or "",
            )
        if data_type == "list_recall":
            return _render_list_recall(data, lang)
        if data_type == "list_disambiguation":
            return _render_list_disambiguation(data.get("candidates") or [], lang)

    # Special case: expense needs a currency answer — not a real error.
    if agent == "ExpenseAgent" and data.get("needs_currency"):
        return _NEEDS_CURRENCY.get(lang, _NEEDS_CURRENCY["en"])

    # Agent failed — use distinct error message (not the success fallback).
    # Specific error codes beat the generic agent-level message.
    if not result.success:
        # ListAgent data-driven failures rendered from the result's data
        # context (existing_names, requested_name, list_name, item_count).
        if agent == "ListAgent":
            if result.error_message == "list_not_found":
                return _render_list_not_found(data, lang)
            if result.error_message == "list_cap_reached":
                names = _format_names(data.get("existing_names") or [])
                return _LIST_CAP_REACHED_COPY.get(lang, _LIST_CAP_REACHED_COPY["es"]).format(names=names)
            if result.error_message == "empty_list":
                return _LIST_EMPTY_COPY.get(lang, _LIST_EMPTY_COPY["es"]).format(
                    list_name=data.get("list_name") or "",
                )
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
