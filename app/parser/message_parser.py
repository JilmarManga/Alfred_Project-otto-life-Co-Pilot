import os
import re
import json
import logging
import openai
from typing import Optional

from app.models.parsed_message import ParsedMessage, EventReference
from app.parser.word_number_parser import parse_word_numbers

# Ordinal words → 0-based event index (deterministic, no LLM)
_ORDINAL_TO_INDEX = {
    "primero": 0, "primer": 0, "primera": 0, "first": 0, "1st": 0,
    "segundo": 1, "segunda": 1, "second": 1, "2nd": 1,
    "tercero": 2, "tercer": 2, "tercera": 2, "third": 2, "3rd": 2,
    "cuarto": 3, "cuarta": 3, "fourth": 3, "4th": 3,
    "quinto": 4, "quinta": 4, "fifth": 4, "5th": 4,
    "sexto": 5, "sexta": 5, "sixth": 5, "6th": 5,
    "séptimo": 6, "septimo": 6, "séptima": 6, "seventh": 6, "7th": 6,
    "octavo": 7, "octava": 7, "eighth": 7, "8th": 7,
    "noveno": 8, "novena": 8, "ninth": 8, "9th": 8,
    "décimo": 9, "decimo": 9, "décima": 9, "tenth": 9, "10th": 9,
}

# Keywords that mean "the immediately next upcoming event"
_NEXT_EVENT_KEYWORDS = {"siguiente", "próximo", "próxima", "proximo", "proxima", "next"}


def _parse_event_reference(text: str) -> EventReference | None:
    """Deterministic scan for event references — never uses LLM."""
    words = text.lower().split()
    for word in words:
        clean = word.strip("¿?.,!")
        if clean in _NEXT_EVENT_KEYWORDS:
            return EventReference(time_reference="next")
        if clean in _ORDINAL_TO_INDEX:
            return EventReference(index=_ORDINAL_TO_INDEX[clean])
    # Regex fallback for digit-based ordinals: "11th", "12th", etc.
    match = re.search(r'\b(\d+)(?:st|nd|rd|th)\b', text.lower())
    if match:
        return EventReference(index=int(match.group(1)) - 1)
    # Bare digit + "one/uno" pronoun: "the 2 one", "el 5 uno"
    match = re.search(r'\b(\d+)\s+(?:one|uno|una)\b', text.lower())
    if match:
        return EventReference(index=int(match.group(1)) - 1)

logger = logging.getLogger(__name__)

# Detects clock-time references so the word-number fallback doesn't misread a
# clock hour as a money amount when the LLM correctly returns null.
# Covers explicit formats AND natural language time-of-day phrases (standalone
# or with a number), so "7 de la tarde" and "in the afternoon" both guard correctly.
_CLOCK_TIME_RE = re.compile(
    r"\b\d{1,2}\s*(?:am|pm|hrs?)\b"                               # "7 am", "9pm", "10h"
    r"|\b\d{1,2}:\d{2}\b"                                         # "14:00", "7:30"
    r"|\b(?:a\s+las|at|las)\s+\d{1,2}\b"                         # "a las 7", "at 3", "las 2"
    r"|\b\d{1,2}\s*(?:de la|por la|en la)\s*(?:tarde|noche|mañana|madrugada)\b"  # "7 de la tarde"
    r"|\b(?:de la|por la|en la)\s+(?:tarde|noche|mañana|madrugada)\b"            # standalone "de la tarde"
    r"|\bin the\s+(?:afternoon|morning|evening|night)\b"          # "in the afternoon"
    r"|\bat\s+(?:night|noon|midnight)\b"                          # "at night"
    r"|\bthis\s+(?:morning|afternoon|evening|night)\b"            # "this morning"
    r"|\btonight\b|\besta\s+(?:noche|tarde|mañana)\b",            # "tonight", "esta noche/tarde/mañana"
    re.IGNORECASE,
)

openai.api_key = os.getenv("OPENAI_API_KEY")
GPT_MODEL = "gpt-4o-mini"

# Deterministic signal keyword sets (from CLAUDE.md)
CALENDAR_KEYWORDS  = {"calendario", "calendar", "agenda", "reunion", "reunión", "meeting", "event", "evento", "tengo", "schedule", "have", "day", "busy"}
WEATHER_KEYWORDS   = {"clima", "weather", "lluvia", "temperatura", "temperature", "rain", "calor", "frio",
                       "llover", "lloverá", "llueve", "raining",
                       "be hot", "is it hot", "too hot", "so hot", "how hot", "getting hot"}
SUMMARY_KEYWORDS   = {"resumen", "summary", "cuanto", "cuánto", "gaste", "gasté", "spent", "gastos", "expenses",
                       "wasted", "waste", "spend", "money", "dinero", "plata", "gastado"}
TRAVEL_KEYWORDS    = {"llegar", "llego", "tiempo", "tráfico", "trafico", "traffic", "travel", "arrive", "salir", "leave"}
GREETING_KEYWORDS  = {"hola", "hello", "hey", "buenos días", "buenos dias",
                       "good morning", "buenas tardes", "good afternoon",
                       "buenas noches", "good evening", "buenas", "que tal", "qué tal"}
GRATITUDE_KEYWORDS = {"gracias", "thanks", "thank you", "thankss", "thanx", "grax", "tks"}
CREATE_KEYWORDS    = {
    # Spanish
    "agendar", "agendalo", "agéndalo",
    "agenda una", "agenda un", "agenda el", "agenda mi",
    "crea una", "crea un", "crea el", "crea mi", "crea la",
    "crear una", "crear un", "crear el", "crear mi", "crear la",
    "agrega", "agrega una", "agrega un", "agrega el", "agrega mi",
    "agregar al calendario", "añade al calendario", "añadir al calendario",
    "programa", "programar",
    "programa una", "programa un", "programar una", "programar un",
    "nueva reunión", "nuevo evento",
    # English
    "add event", "add a meeting", "add an event",
    "create event", "create meeting", "create a meeting", "create an event",
    "schedule a", "schedule an", "schedule my",
    "program a", "program an", "program my",
    "book a", "book an", "book me",
    "set up a meeting", "new meeting", "new event",
    "put it on my calendar", "add to my calendar",
}
# Reminder opt-out / opt-in phrases. Kept as multi-word phrases (not bare
# "stop" or "off") so they never collide with unrelated messages.
REMINDER_OFF_KEYWORDS = {
    # Spanish
    "recordatorios off", "desactivar recordatorios", "desactiva recordatorios",
    "desactivar los recordatorios", "quitar recordatorios", "quita recordatorios",
    "sin recordatorios", "apaga recordatorios", "apagar recordatorios",
    # English
    "turn off reminders", "stop reminders", "disable reminders",
    "mute reminders", "no more reminders",
}
REMINDER_ON_KEYWORDS = {
    # Spanish
    "recordatorios on", "activar recordatorios", "activa recordatorios",
    "activar los recordatorios", "reactivar recordatorios",
    "enciende recordatorios", "encender recordatorios",
    # English
    "turn on reminders", "enable reminders", "start reminders",
    "resume reminders",
}

EXTRACTION_PROMPT = """You are a data extractor for a personal assistant (finance, calendar, weather, travel).
Given a user message in any language (primarily Spanish and English),
extract ONLY a structured JSON object. Do not classify intent.
Do not decide what to do. Only extract.

Rules:
- Convert ALL number words to digits: "dos" → 2, "dos millones" → 2000000,
  "two hundred" → 200, "veinte mil" → 20000
- If no amount is present, set amount to null
- IMPORTANT: Clock times like "2pm", "las 3", "14:00", "a las 8", "at 9", "9am"
  are NOT amounts. Always set amount=null when the number is a time reference.
- Extract category_hint from context clues (e.g. "arriendo" → "housing", "comida" → "food")
- For currency: return "COP", "USD", "EUR", or null. If unclear, return null.

Event creation — fill these fields ONLY when the user is DESCRIBING a new specific
event to add to their calendar (e.g. "meeting with John tomorrow at 3pm at the office",
"agenda reunión mañana 2pm en Titan"):
- event_title: short title of the event, or null
- event_start: ISO 8601 datetime with tz offset (e.g. "2026-04-22T14:00:00-05:00"),
  or null. Use the "Today" and "Timezone" context (if provided) to resolve relative
  dates like "mañana", "next Wednesday", "el viernes" to absolute dates.
  IMPORTANT: A bare day name ("viernes", "lunes", "friday", "monday", etc.) always
  means the NEXT upcoming occurrence of that day from Today's date. Never pick a
  past date for a day name.
- event_location: the place, or null
- event_duration_minutes: integer minutes if user specifies duration, or null

DO NOT fill event fields when the user is asking a question about existing events
("tengo reunión?", "do I have a meeting?", "cuál es mi próximo evento?", "¿qué tengo hoy?").
DO NOT fill event fields when the user references events vaguely ("next event",
"the first one", "y el segundo?").

- Return ONLY valid JSON. No preamble. No explanation.

Output format:
{ "amount": <number or null>, "currency": <"COP"|"USD"|"EUR"|null>,
  "category_hint": <string or null>, "date_hint": <string or null>,
  "event_title": <string or null>, "event_start": <ISO 8601 string or null>,
  "event_location": <string or null>, "event_duration_minutes": <integer or null>,
  "raw_message": <original message> }"""


def _scan_signals(text: str) -> list[str]:
    """Deterministic keyword scan — no LLM involved."""
    text_lower = text.lower()
    found = []
    for kw in (CALENDAR_KEYWORDS | WEATHER_KEYWORDS | SUMMARY_KEYWORDS | TRAVEL_KEYWORDS
               | GREETING_KEYWORDS | GRATITUDE_KEYWORDS | CREATE_KEYWORDS
               | REMINDER_OFF_KEYWORDS | REMINDER_ON_KEYWORDS):
        if kw in text_lower:
            found.append(kw)
    return found


def _heuristic_parse(raw_text: str) -> ParsedMessage:
    """Fallback when LLM fails — regex + word_number_parser."""
    text = raw_text.lower()
    amount: Optional[float] = None

    money_match = re.search(r"\$?\s?(\d[\d.,]*)", text)
    if money_match:
        try:
            amount = float(money_match.group(1).replace(",", ""))
        except ValueError:
            pass

    if amount is None:
        amount = parse_word_numbers(text)

    event_reference = _parse_event_reference(raw_text)
    if event_reference is not None:
        amount = None

    return ParsedMessage(
        amount=amount,
        currency=None,
        category_hint=None,
        date_hint=None,
        raw_message=raw_text,
        signals=_scan_signals(raw_text),
        event_reference=event_reference,
    )


async def parse_message(raw_text: str, user_context: dict = None) -> ParsedMessage:
    """
    Layer 1: Convert raw WhatsApp text → ParsedMessage.
    LLM extracts fields; signals are populated deterministically.

    user_context (optional): {"today": "YYYY-MM-DD", "tz": "America/Bogota"}
    Used by the LLM to resolve relative dates like "next Wednesday" to absolute ISO.
    """
    if not raw_text or not raw_text.strip():
        return ParsedMessage(raw_message=raw_text or "", signals=[])

    system_prompt = EXTRACTION_PROMPT
    if user_context:
        today = user_context.get("today")
        tz = user_context.get("tz") or "UTC"
        if today:
            system_prompt = (
                f"Current context (for relative date resolution):\n"
                f"- Today's date: {today}\n"
                f"- User timezone: {tz}\n\n"
                + EXTRACTION_PROMPT
            )

    try:
        response = openai.chat.completions.create(
            model=GPT_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": raw_text},
            ],
            temperature=0.0,
        )

        content = response.choices[0].message.content.strip()
        if content.startswith("```"):
            content = re.sub(r"```[a-z]*", "", content).replace("```", "").strip()

        data = json.loads(content)

        raw_amount = data.get("amount")
        amount: Optional[float] = None
        if raw_amount is not None:
            try:
                amount = float(raw_amount)
            except (TypeError, ValueError):
                amount = parse_word_numbers(str(raw_amount))

        # Post-process: if LLM missed word-numbers, try parser on raw text.
        # Skip when the message contains a clock time — the LLM returned null
        # deliberately (e.g. "a las 7 am") and the digit is not a money amount.
        if amount is None and not _CLOCK_TIME_RE.search(raw_text):
            amount = parse_word_numbers(raw_text)

        currency = data.get("currency")
        if currency:
            currency = currency.upper()
            if currency not in {"COP", "USD", "EUR"}:
                currency = None

        event_reference = _parse_event_reference(raw_text)

        event_title = data.get("event_title")
        event_start = data.get("event_start")
        event_location = data.get("event_location")
        raw_duration = data.get("event_duration_minutes")
        event_duration_minutes: Optional[int] = None
        if raw_duration is not None:
            try:
                event_duration_minutes = int(raw_duration)
            except (TypeError, ValueError):
                event_duration_minutes = None

        # Creation intent wins over misread amount/event_reference.
        # If LLM extracted a concrete event (title + start), the numeric
        # component was a clock time, not a money amount.
        if event_title and event_start:
            amount = None
            event_reference = None
        elif event_reference is not None:
            amount = None

        return ParsedMessage(
            amount=amount,
            currency=currency,
            category_hint=data.get("category_hint"),
            date_hint=data.get("date_hint"),
            raw_message=raw_text,
            signals=_scan_signals(raw_text),  # deterministic, never from LLM
            event_reference=event_reference,
            event_title=event_title,
            event_start=event_start,
            event_location=event_location,
            event_duration_minutes=event_duration_minutes,
        )

    except Exception as e:
        logger.warning("LLM parse failed, falling back to heuristic: %s", e)
        return _heuristic_parse(raw_text)
