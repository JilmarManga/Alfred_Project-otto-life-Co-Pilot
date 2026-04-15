import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Optional

import openai

logger = logging.getLogger(__name__)

openai.api_key = os.getenv("OPENAI_API_KEY")
GPT_MODEL = "gpt-4o-mini"

_EXTRACTION_PROMPT = """You extract a user's first name and city from an onboarding reply.
The reply may be in Spanish or English. It may contain only a name, only a city, or both.

Rules:
- "name" is the user's first name only. Strip titles, last names, emojis.
- "city" is the user's raw city/location string, exactly as they wrote it. Do NOT normalize, translate, or add a country.
- If a field is missing, set it to null.
- Return ONLY valid JSON. No preamble.

Output format:
{ "name": <string or null>, "city": <string or null> }"""


@dataclass
class NameCityExtraction:
    name: Optional[str]
    city: Optional[str]


def _heuristic_extract(raw_text: str) -> NameCityExtraction:
    """Deterministic fallback used when the LLM call fails or returns garbage."""
    text = (raw_text or "").strip()
    if not text:
        return NameCityExtraction(name=None, city=None)

    if "," in text:
        parts = [p.strip() for p in text.split(",") if p.strip()]
        if len(parts) >= 2:
            name = parts[0].split()[0] if parts[0] else None
            city = ", ".join(parts[1:])
            return NameCityExtraction(name=name, city=city)

    m = re.search(
        r"(?:soy|me llamo|mi nombre es|i am|i'm|my name is)\s+([A-Za-zÀ-ÿ]+)(?:\s+(?:de|from|in)\s+(.+))?",
        text,
        re.IGNORECASE,
    )
    if m:
        return NameCityExtraction(name=m.group(1), city=(m.group(2) or "").strip() or None)

    tokens = text.split()
    if len(tokens) == 1:
        return NameCityExtraction(name=tokens[0], city=None)

    if len(tokens) == 2:
        return NameCityExtraction(name=tokens[0], city=tokens[1])

    return NameCityExtraction(name=None, city=None)


async def extract_name_and_city(raw_text: str) -> NameCityExtraction:
    """
    Onboarding-only utility: extract first name and raw city string from the
    user's first profile message. Never raises — always returns a result.
    """
    if not raw_text or not raw_text.strip():
        return NameCityExtraction(name=None, city=None)

    try:
        response = openai.chat.completions.create(
            model=GPT_MODEL,
            messages=[
                {"role": "system", "content": _EXTRACTION_PROMPT},
                {"role": "user", "content": raw_text},
            ],
            temperature=0.0,
        )
        content = response.choices[0].message.content.strip()
        if content.startswith("```"):
            content = re.sub(r"```[a-z]*", "", content).replace("```", "").strip()

        data = json.loads(content)
        name = data.get("name")
        city = data.get("city")

        if isinstance(name, str):
            name = name.strip().split()[0] if name.strip() else None
        else:
            name = None

        if isinstance(city, str):
            city = city.strip() or None
        else:
            city = None

        if name is None and city is None:
            return _heuristic_extract(raw_text)

        return NameCityExtraction(name=name, city=city)

    except Exception as exc:
        logger.warning("name_city_extractor LLM failed, falling back: %s", exc)
        return _heuristic_extract(raw_text)
