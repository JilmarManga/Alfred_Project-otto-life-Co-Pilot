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
    "food": [
        "almuerzo", "desayuno", "cena", "comida", "pan", "restaurante",
        "coffee", "café", "cafe", "lunch", "breakfast", "dinner", "food"
    ],
    "transport": [
        "uber", "taxi", "bus", "metro", "moto", "gasolina", "gas",
        "transporte", "peaje", "pasaje", "fuel", "ride"
    ],
    "shopping": [
        "tienda", "compré", "comprar", "ropa", "zapatos", "mercado",
        "shopping", "clothes", "shoes", "store", "mall"
    ],
    "health": [
        "medicina", "doctor", "hospital", "salud", "farmacia",
        "medicine", "pharmacy", "health", "clinic"
    ],
}

def normalize_currency_and_amount(amount, currency, user_context=None):
    if user_context is None:
        user_context = {}

    if currency and currency.lower() == "mil":
        amount = amount * 1000
        currency = "COP"

    if currency not in ["USD", "COP"]:
        if amount and amount >= 1000:
            currency = "COP"
        else:
            currency = "USD"

    return amount, currency

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

    # Fallback category assignment using keywords
    category = "other"
    for cat, keywords in CATEGORY_KEYWORDS.items():
        if any(word in text for word in keywords):
            category = cat
            break

    return ExtractedExpense(
        amount=amount,
        currency=currency,
        category=category,
        description=text,
        confidence=0.4,
        user_message=text
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
        # Clean possible markdown code fences from GPT response
        if content.startswith("```"):
            content = content.replace("```json", "").replace("```", "").strip()
        # Try to parse JSON
        import json
        print("🧪 RAW GPT RESPONSE:", content)
        data = json.loads(content)

        amount = float(data.get("amount")) if data.get("amount") else None
        currency = data.get("currency")
        category = data.get("category")

        # Validate keys
        # amount = float(data.get("amount")) if data.get("amount") else None

        # currency = data.get("currency")
        # Validate and normalize currency (MVP safe logic)
        allowed_currencies = {"USD", "COP"}

        if currency:
            currency = currency.upper()

        if currency not in allowed_currencies:
            # Infer currency from text + amount
            text_lower = text.lower()

            if "usd" in text_lower or "$" in text_lower or "dolar" in text_lower:
                currency = "USD"
            elif "cop" in text_lower or "peso" in text_lower:
                currency = "COP"
            elif amount and amount >= 1000:
                currency = "COP"
            else:
                currency = "USD"

        amount, currency = normalize_currency_and_amount(amount, currency)

        # Smart category override: if GPT returns low confidence category but we detect keywords, use them
        if not category:
            category = "other"
        text_lower = text.lower()
        for cat, keywords in CATEGORY_KEYWORDS.items():
            for keyword in keywords:
                if keyword in text_lower:
                    category = cat
                    break
            if category == cat:
                break

        # Normalize and validate category
        allowed_categories = set(CATEGORY_KEYWORDS.keys()) | {"other"}

        if not category or category not in allowed_categories:
            # Fallback to keyword-based category
            text_lower = text.lower()
            category = "other"
            for cat, keywords in CATEGORY_KEYWORDS.items():
                if any(word in text_lower for word in keywords):
                    category = cat
                    break

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
            user_message=text
        )

    except Exception as e:
        # Fallback to heuristic if GPT fails or returns invalid data
        print("❌ GPT parsing failed:", e)
        print("⚠️ Falling back to heuristic")
        return heuristic_extract(message)