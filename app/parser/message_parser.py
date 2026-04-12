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

openai.api_key = os.getenv("OPENAI_API_KEY")
GPT_MODEL = "gpt-4o-mini"

# Deterministic signal keyword sets (from CLAUDE.md)
CALENDAR_KEYWORDS = {"calendario", "agenda", "reunion", "reunión", "meeting", "event", "evento", "tengo", "schedule", "have", "day", "busy"}
WEATHER_KEYWORDS  = {"clima", "weather", "lluvia", "temperatura", "temperature", "rain", "calor", "frio"}
SUMMARY_KEYWORDS  = {"resumen", "summary", "cuanto", "cuánto", "gaste", "gasté", "spent", "gastos", "expenses"}
TRAVEL_KEYWORDS   = {"llegar", "llego", "tiempo", "tráfico", "trafico", "traffic", "travel", "arrive", "salir", "leave"}

EXTRACTION_PROMPT = """You are a data extractor for a personal finance assistant.
Given a user message in any language (primarily Spanish and English),
extract ONLY a structured JSON object. Do not classify intent.
Do not decide what to do. Only extract.

Rules:
- Convert ALL number words to digits: "dos" → 2, "dos millones" → 2000000,
  "two hundred" → 200, "veinte mil" → 20000
- If no amount is present, set amount to null
- Extract category_hint from context clues (e.g. "arriendo" → "housing", "comida" → "food")
- For currency: return "COP", "USD", "EUR", or null. If unclear, return null.
- Return ONLY valid JSON. No preamble. No explanation.

Output format:
{ "amount": <number or null>, "currency": <"COP"|"USD"|"EUR"|null>,
  "category_hint": <string or null>, "date_hint": <string or null>,
  "raw_message": <original message> }"""


def _scan_signals(text: str) -> list[str]:
    """Deterministic keyword scan — no LLM involved."""
    text_lower = text.lower()
    found = []
    for kw in CALENDAR_KEYWORDS | WEATHER_KEYWORDS | SUMMARY_KEYWORDS | TRAVEL_KEYWORDS:
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
    """
    if not raw_text or not raw_text.strip():
        return ParsedMessage(raw_message=raw_text or "", signals=[])

    try:
        response = openai.chat.completions.create(
            model=GPT_MODEL,
            messages=[
                {"role": "system", "content": EXTRACTION_PROMPT},
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

        # Post-process: if LLM missed word-numbers, try parser on raw text
        if amount is None:
            amount = parse_word_numbers(raw_text)

        currency = data.get("currency")
        if currency:
            currency = currency.upper()
            if currency not in {"COP", "USD", "EUR"}:
                currency = None

        event_reference = _parse_event_reference(raw_text)
        if event_reference is not None:
            amount = None

        return ParsedMessage(
            amount=amount,
            currency=currency,
            category_hint=data.get("category_hint"),
            date_hint=data.get("date_hint"),
            raw_message=raw_text,
            signals=_scan_signals(raw_text),  # deterministic, never from LLM
            event_reference=event_reference,
        )

    except Exception as e:
        logger.warning("LLM parse failed, falling back to heuristic: %s", e)
        return _heuristic_parse(raw_text)
