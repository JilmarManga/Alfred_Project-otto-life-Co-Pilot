import logging
from typing import Optional

from app.db.user_context_store import get_user_context, update_user_context
from app.models.extracted_expense import ExtractedExpense
from app.models.inbound_message import InboundMessage
from app.repositories.expense_repository import ExpenseRepository
from app.repositories.user_repository import UserRepository
from app.services.whatsapp_sender import send_whatsapp_message

logger = logging.getLogger(__name__)

_VALID_CURRENCIES = {"COP", "USD", "EUR"}

_CURRENCY_ALIASES = {
    "cop": "COP", "peso": "COP", "pesos": "COP", "colombianos": "COP", "colombian": "COP",
    "usd": "USD", "dolar": "USD", "dolares": "USD", "dólar": "USD", "dólares": "USD",
    "dollar": "USD", "dollars": "USD", "us": "USD",
    "eur": "EUR", "euro": "EUR", "euros": "EUR",
}


def _detect_currency(text: str) -> Optional[str]:
    upper = (text or "").strip().upper()
    if upper in _VALID_CURRENCIES:
        return upper
    for token in (text or "").lower().replace(",", " ").split():
        if token in _CURRENCY_ALIASES:
            return _CURRENCY_ALIASES[token]
    return None


def handle_pending_expense(inbound: InboundMessage, user: Optional[dict]) -> bool:
    """
    If the user has a pending expense waiting for a currency answer,
    consume this message as the currency and finalize the save.
    Returns True if consumed, False to fall through to the normal pipeline.
    """
    if not user:
        return False

    phone = inbound.user_phone_number
    ctx = get_user_context(phone)
    pending = ctx.get("pending_expense")
    if not pending:
        return False

    text = inbound.text or ""
    currency = _detect_currency(text)
    lang = (user.get("language") or "en").lower()

    if not currency:
        # A reply longer than 4 words means the user moved on to a new topic.
        # Drop the stash and let the normal pipeline handle it.
        if len(text.split()) > 4:
            update_user_context(phone, "pending_expense", None)
            return False
        msg = (
            "Which currency was that? Reply with COP, USD, or EUR 🙏"
            if lang == "en"
            else "¿En qué moneda fue? Responde COP, USD o EUR 🙏"
        )
        send_whatsapp_message(phone, msg)
        return True

    try:
        expense = ExtractedExpense(
            amount=pending["amount"],
            currency=currency,
            category=pending.get("category") or "other",
            description=pending.get("raw_message", ""),
            confidence=0.9,
        )
        ExpenseRepository.save_expense(user_phone_number=phone, expense=expense)
        UserRepository.create_or_update_user(phone, {"preferred_currency": currency})
    except Exception as exc:
        logger.error("Failed to finalize pending expense for %s: %s", phone, exc)
        fallback = (
            "Couldn't save that expense. Try again 🙏"
            if lang == "en"
            else "No pude guardar ese gasto. Intenta de nuevo 🙏"
        )
        send_whatsapp_message(phone, fallback)
        update_user_context(phone, "pending_expense", None)
        return True

    update_user_context(phone, "pending_expense", None)
    send_whatsapp_message(phone, "👍 Saved." if lang == "en" else "👍 Anotado.")
    return True
