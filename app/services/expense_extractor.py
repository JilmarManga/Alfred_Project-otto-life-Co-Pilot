import os
import re
import openai
from typing import Optional

from app.models.inbound_message import InboundMessage
from app.models.extracted_expense import ExtractedExpense

# Configure OpenAI API key
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
openai.api_key = OPENAI_API_KEY

CATEGORY_KEYWORDS = {
    "food": ["almuerzo", "desayuno", "cena", "coffee", "café", "cafe", "lunch", "breakfast", "dinner"],
    "transport": ["uber", "taxi", "bus", "metro", "moto", "gasolina", "gas", "transporte"],
    "shopping": ["tienda", "shopping", "compré", "comprar", "ropa", "zapatos"],
    "health": ["medicina", "doctor", "hospital", "salud", "farmacia"],
}

GPT_MODEL = "gpt-4o-mini"  # Current model used.

def heuristic_extract(message: InboundMessage) -> ExtractedExpense:
    """
    Simple fallback extractor used if the LLM fails.
    Detects basic amount and returns minimal structured data.
    """
    text = (message.text or "").lower()

    amount = None
    currency = None

    money_match = re.search(r"(\$?\s?\d[\d.,]*)", text)
    if money_match:
        raw = money_match.group(1)
        raw = raw.replace("$", "").replace(",", "")
        try:
            amount = float(raw)
            currency = "USD"
        except Exception:
            amount = None

    return ExtractedExpense(
        amount=amount,
        currency=currency,
        category="other",
        description=text,
        confidence=0.4,
    )


async def extract_expense(message: InboundMessage) -> ExtractedExpense:
    """
    GPT-powered expense extraction with fallback to local heuristics.

    Returns an ExtractedExpense object with validated confidence.
    """
    text = (message.text or "").strip()
    if not text:
        return heuristic_extract(message)

    # Prepare the prompt for GPT
    prompt = f"""
    Extract structured expense data from the following user message.
    Return JSON with keys: amount (float), currency (str), category (food, transport, shopping, health, other), 
    description (original text), confidence (0-1).

    Message:
    \"\"\"{text}\"\"\"
    """

    # Call GPT
    try:
        response = openai.chat.completions.create(
            model=GPT_MODEL,
            messages=[
                {"role": "system", "content": "You are an assistant that extracts expenses in JSON format."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.0
        )

        content = response.choices[0].message.content.strip()
        # Try to parse JSON
        import json
        data = json.loads(content)

        # Validate keys
        amount = float(data.get("amount")) if data.get("amount") else None
        currency = data.get("currency")
        category = data.get("category")
        description = data.get("description") or text
        confidence = float(data.get("confidence", 0.0))

        # Validate confidence
        if not (0.0 <= confidence <= 1.0):
            raise ValueError("Confidence out of bounds")

        return ExtractedExpense(
            amount=amount,
            currency=currency,
            category=category,
            description=description,
            confidence=confidence,
        )

    except Exception as e:
        # Fallback to heuristic if GPT fails or returns invalid data
        return heuristic_extract(message)