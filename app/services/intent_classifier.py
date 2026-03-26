import re

from app.models.inbound_message import InboundMessage
from app.models.message_intent import MessageIntent


EXPENSE_KEYWORDS = {
    "gasté",
    "gaste",
    "spent",
    "pagué",
    "pague",
    "paid",
    "uber",
    "taxi",
    "gasolina",
    "gas",
    "coffee",
    "café",
    "cafe",
    "almuerzo",
    "lunch",
    "desayuno",
    "breakfast",
    "dinner",
    "cena",
}

MONEY_SYMBOLS = {"$", "€", "£"}

CURRENCY_KEYWORDS = {
    "cop",
    "peso",
    "pesos",
    "usd",
    "dollar",
    "dolares",
    "dolar",
    "dollars",
    "eur",
    "euro",
    "euros",
}


def _looks_like_money_amount(text: str) -> bool:
    """
    Detect whether the text contains a likely money amount.

    Examples:
    - $20
    - 20000 cop
    - 25 usd
    - 15 mil
    - 18.500
    """
    currency_pattern = "|".join(sorted(re.escape(word) for word in CURRENCY_KEYWORDS))
    symbol_pattern = "|".join(re.escape(symbol) for symbol in MONEY_SYMBOLS)

    money_pattern = re.compile(
        rf"(({symbol_pattern})\s?\d[\d.,]*)|(\d[\d.,]*\s?({currency_pattern}))|(\d[\d.,]*\s?mil)",
        re.IGNORECASE,
    )
    return bool(money_pattern.search(text))


def classify_message_intent(message: InboundMessage) -> MessageIntent:
    """
    Classify an inbound message into otto's current MVP intents.

    This first version uses lightweight deterministic heuristics to detect
    likely expense messages from text content only.
    """
    text = (message.text or "").strip().lower()

    # Calendar intent detection
    if any(word in text for word in [
        "calendar", "meeting", "event", "agenda", "schedule",
        "hoy", "today", "reunión", "reunion", "cita"
    ]):
        return MessageIntent(intent="calendar_query", confidence=0.9)

    if not text:
        return MessageIntent(intent="unknown", confidence=0.0)

    has_number = bool(re.search(r"\d+", text))
    has_expense_keyword = any(keyword in text for keyword in EXPENSE_KEYWORDS)
    has_money_pattern = _looks_like_money_amount(text)

    if has_expense_keyword and has_money_pattern:
        return MessageIntent(intent="expense", confidence=0.95)

    if has_money_pattern:
        return MessageIntent(intent="expense", confidence=0.75)

    if has_number and has_expense_keyword:
        return MessageIntent(intent="expense", confidence=0.80)

    return MessageIntent(intent="unknown", confidence=0.2)