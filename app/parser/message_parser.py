import os
import re
import json
import logging
import openai
from typing import Optional

from app.models.parsed_message import ParsedMessage, EventReference
from app.parser.word_number_parser import parse_word_numbers

logger = logging.getLogger(__name__)

openai.api_key = os.getenv("OPENAI_API_KEY")
GPT_MODEL = "gpt-4o-mini"

# Deterministic signal keyword sets (from CLAUDE.md)
CALENDAR_KEYWORDS = {"calendario", "agenda", "reunion", "reunión", "meeting", "event", "evento", "tengo"}
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

    return ParsedMessage(
        amount=amount,
        currency=None,
        category_hint=None,
        date_hint=None,
        raw_message=raw_text,
        signals=_scan_signals(raw_text),
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

        return ParsedMessage(
            amount=amount,
            currency=currency,
            category_hint=data.get("category_hint"),
            date_hint=data.get("date_hint"),
            raw_message=raw_text,
            signals=_scan_signals(raw_text),  # deterministic, never from LLM
        )

    except Exception as e:
        logger.warning("LLM parse failed, falling back to heuristic: %s", e)
        return _heuristic_parse(raw_text)
