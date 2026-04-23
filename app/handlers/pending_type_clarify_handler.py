import logging
import re
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from app.db.user_context_store import get_user_context, update_user_context
from app.models.inbound_message import InboundMessage
from app.services.whatsapp_sender import send_whatsapp_message

logger = logging.getLogger(__name__)

# Replies that mean "yes, add to the calendar"
_CALENDAR_PHRASES = {
    "add to my calendar", "add to calendar", "al calendario",
    "a mi calendario", "en el calendario", "a la agenda", "en la agenda",
    "yes please", "sí please",
}
_CALENDAR_TOKENS = {
    "calendar", "calendario", "agenda", "cita", "appointment", "evento", "event",
    "sí", "si", "yes", "yep", "yeah", "dale", "ok", "okay", "listo",
    "claro", "sure", "please", "por favor",
}

# Replies that mean "no, treat as an expense"
_EXPENSE_TOKENS = {
    "expense", "gasto", "expenses", "gastos", "no", "nah", "nope", "nop",
    "dinero", "money", "cost", "costo", "pago",
}

_NOT_CONNECTED = {
    "es": "No pude conectar tu calendario. Termina la configuración 🙏",
    "en": "I couldn't reach your calendar. Please finish the setup 🙏",
}
_CALENDAR_MISSING_DETAILS = {
    "es": "Entendido 📅 ¿A qué hora y fecha es? Dímelo y lo agendo.",
    "en": "Got it 📅 What date and time is it? Tell me and I'll add it.",
}
_CREATE_ERROR = {
    "es": "No pude crear el evento. Intenta de nuevo 🙏",
    "en": "Couldn't create the event. Try again 🙏",
}
_EXPENSE_SAVE_ERROR = {
    "es": "No pude guardar ese gasto. Intenta de nuevo 🙏",
    "en": "Couldn't save that expense. Try again 🙏",
}


def _classify(text: str) -> str:
    """Return 'calendar', 'expense', or 'other'."""
    lower = (text or "").lower().strip()
    if not lower:
        return "other"

    # Messages longer than 5 words mean the user moved on to a new topic.
    if len(lower.split()) > 5:
        return "other"

    # Multi-word phrase check first
    for phrase in _CALENDAR_PHRASES:
        if phrase in lower:
            return "calendar"

    # Strip punctuation and check individual tokens
    tokens = set(re.sub(r"[¿?.,!;:()\"]", " ", lower).split())

    # Expense tokens win over calendar tokens to avoid "no" being misread
    if tokens & _EXPENSE_TOKENS:
        return "expense"
    if tokens & _CALENDAR_TOKENS:
        return "calendar"

    return "other"


def _infer_start(date_hint: str, hour: int, user_tz: str) -> Optional[str]:
    """Construct an ISO 8601 start from a simple date hint + clock hour."""
    try:
        tz = ZoneInfo(user_tz or "UTC")
        today = datetime.now(tz).date()
        hint = (date_hint or "").lower().strip()
        if hint in ("mañana", "tomorrow"):
            target = today + timedelta(days=1)
        elif hint in ("hoy", "today"):
            target = today
        else:
            return None
        return datetime(target.year, target.month, target.day, hour, 0, tzinfo=tz).isoformat()
    except Exception:
        return None


def handle_pending_type_clarify(inbound: InboundMessage, user: Optional[dict]) -> bool:
    """
    Pre-pipeline gate: if the user has a pending expense/calendar disambiguation
    stashed, interpret their reply and either create the calendar event or save
    the expense. Returns True if consumed, False to fall through to pipeline.
    """
    if not user:
        return False

    phone = inbound.user_phone_number
    ctx = get_user_context(phone)
    pending = ctx.get("pending_type_clarify")
    if not pending:
        return False

    intent = _classify(inbound.text or "")
    lang = (user.get("language") or "es").lower()

    if intent == "other":
        update_user_context(phone, "pending_type_clarify", None)
        return False

    update_user_context(phone, "pending_type_clarify", None)

    if intent == "expense":
        _finalize_expense(phone, pending, user, lang)
    else:  # calendar
        _finalize_calendar(phone, pending, user, lang)

    return True


def _finalize_expense(phone, pending, user, lang):
    from app.models.extracted_expense import ExtractedExpense
    from app.repositories.expense_repository import ExpenseRepository
    from app.repositories.user_repository import UserRepository

    preferred_currency = user.get("preferred_currency")
    if not preferred_currency:
        # Fall back to pending_expense flow to ask for currency
        update_user_context(phone, "pending_expense", {
            "amount": pending["amount"],
            "category": pending.get("category") or "other",
            "raw_message": pending.get("raw_message", ""),
        })
        msg = (
            "¿En qué moneda fue? Responde COP, USD o EUR 🙏"
            if lang == "es"
            else "Which currency was that? Reply with COP, USD, or EUR 🙏"
        )
        send_whatsapp_message(phone, msg)
        return

    try:
        expense = ExtractedExpense(
            amount=pending["amount"],
            currency=preferred_currency,
            category=pending.get("category") or "other",
            description=pending.get("raw_message", ""),
            confidence=0.9,
        )
        ExpenseRepository.save_expense(user_phone_number=phone, expense=expense)
        send_whatsapp_message(phone, "👍 Anotado." if lang == "es" else "👍 Saved.")
    except Exception as exc:
        logger.exception("Type-clarify expense save failed for %s: %s", phone, exc)
        send_whatsapp_message(phone, _EXPENSE_SAVE_ERROR.get(lang, _EXPENSE_SAVE_ERROR["es"]))


def _finalize_calendar(phone, pending, user, lang):
    from app.models.agent_result import AgentResult
    from app.responder.response_formatter import format_response
    from app.services.google_calendar import create_event_for_user
    from app.services.token_crypto import decrypt

    encrypted = user.get("google_calendar_refresh_token")
    if not encrypted:
        send_whatsapp_message(phone, _NOT_CONNECTED.get(lang, _NOT_CONNECTED["es"]))
        return

    try:
        refresh_token = decrypt(encrypted)
    except Exception as exc:
        logger.exception("Token decrypt failed for %s: %s", phone, exc)
        send_whatsapp_message(phone, _NOT_CONNECTED.get(lang, _NOT_CONNECTED["es"]))
        return

    event_title = pending.get("event_title")
    event_location = pending.get("event_location")
    date_hint = pending.get("date_hint")
    amount = pending.get("amount")

    # Try to infer start from date_hint (e.g. "mañana") + amount as clock hour
    event_start = None
    if amount is not None and date_hint:
        event_start = _infer_start(date_hint, int(amount), user.get("timezone") or "UTC")

    if not event_title or not event_start:
        # Can't determine full details — ask user to re-state
        send_whatsapp_message(phone, _CALENDAR_MISSING_DETAILS.get(lang, _CALENDAR_MISSING_DETAILS["es"]))
        return

    try:
        start_dt = datetime.fromisoformat(event_start)
        end_dt = start_dt + timedelta(minutes=60)
        tz_str = user.get("timezone") or "UTC"

        event = create_event_for_user(
            refresh_token,
            title=event_title,
            start_iso=start_dt.isoformat(),
            end_iso=end_dt.isoformat(),
            timezone_str=tz_str,
            location=event_location,
        )

        user_for_formatter = {**user, "phone_number": phone}
        result = AgentResult(
            agent_name="CalendarAgent",
            success=True,
            data={
                "type": "calendar_create",
                "title": event_title,
                "start": start_dt.isoformat(),
                "location": event_location,
                "event_id": event.get("id"),
            },
        )
        reply = format_response(result, user_for_formatter)
        send_whatsapp_message(phone, reply)

        follow_up = "¿Quieres más detalles? 🐙" if lang == "es" else "Want more details? 🐙"
        send_whatsapp_message(phone, follow_up)

    except Exception as exc:
        logger.exception("Type-clarify calendar create failed for %s: %s", phone, exc)
        send_whatsapp_message(phone, _CREATE_ERROR.get(lang, _CREATE_ERROR["es"]))
