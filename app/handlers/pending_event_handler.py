import logging
import unicodedata
from datetime import datetime, timedelta
from typing import Optional

from app.db.user_context_store import get_user_context, update_user_context
from app.models.agent_result import AgentResult
from app.models.inbound_message import InboundMessage
from app.responder.response_formatter import format_response
from app.services.google_calendar import (
    create_event_for_user,
    format_events_detailed,
    get_today_events_for_user,
    normalize_events,
    summarize_day,
)
from app.services.token_crypto import decrypt
from app.services.whatsapp_sender import send_whatsapp_message

logger = logging.getLogger(__name__)

# Explicit calendar-creation phrases that confirm "yes, add this" even in a long
# reply — e.g. "No es un gasto, quiero que me agregues a mi calendario..."
# Checked before the word-length gate so the stash is not dropped prematurely.
_STRONG_AFFIRM_PHRASES = {
    # Spanish
    "al calendario", "a mi calendario", "en el calendario", "en la agenda",
    "a la agenda", "agrégalo", "agéndalo",
    # English
    "add to my calendar", "add to calendar", "add it to my calendar",
}

# Short replies to the clarify question. We only act when the reply is short AND
# matches one of these sets — longer messages mean the user moved on to a new
# topic, so we clear the stash and let the normal pipeline handle it.
_AFFIRMATIVE_KEYWORDS = {
    # Spanish
    "sí", "si", "sii", "siii", "dale", "hazlo", "hagámoslo", "hagamoslo",
    "créalo", "crealo", "agéndalo", "agendalo", "crea", "crear", "agenda", "agendar",
    "agrega", "agregar", "añade", "añadir",
    "ok", "okay", "vale", "listo", "claro", "por favor",
    # English
    "yes", "yep", "yeah", "sure", "create", "create it", "schedule it",
    "go ahead", "do it", "add it", "please",
}

_QUERY_KEYWORDS = {
    # Spanish
    "solo ver", "ver", "muéstrame", "muestrame", "mostrar", "qué tengo", "que tengo",
    # English
    "just check", "just show", "just see", "show", "see", "check",
    "what do i have", "what's on",
}

_ABORT_KEYWORDS = {
    "no", "nah", "nop", "nope",
    "cancela", "cancelar", "déjalo", "dejalo", "olvida", "olvídalo", "olvidalo",
    "cancel", "nvm", "never mind", "nevermind",
}

_FOLLOW_UP_COPY = {
    "es": "¿Quieres más detalles? 🐙",
    "en": "Want more details? 🐙",
}

_ABORT_ACK = {
    "es": "Listo, lo dejamos así 🙂",
    "en": "Got it, skipping 🙂",
}

_NOT_CONNECTED = {
    "es": "No pude conectar tu calendario. Termina la configuración 🙏",
    "en": "I couldn't reach your calendar. Please finish the setup 🙏",
}

_CREATE_ERROR = {
    "es": "No pude crear el evento. Intenta de nuevo 🙏",
    "en": "Couldn't create the event. Try again 🙏",
}

_QUERY_ERROR = {
    "es": "No pude acceder a tu agenda. Intenta de nuevo 🙏",
    "en": "Couldn't access your calendar. Try again 🙏",
}

# Replies longer than this are treated as a new topic, not a confirmation.
_MAX_CONFIRMATION_WORDS = 6


def _strip_accents(s: str) -> str:
    """Remove combining marks so 'créate' matches 'create', 'agéndalo' matches
    'agendalo', etc. iOS Spanish autocorrect frequently inserts/relocates
    accents on short confirmation replies."""
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


# Pre-compute accent-stripped keyword sets at import time. Both the user's
# input and the keywords are normalized so matches are accent-insensitive.
_STRONG_AFFIRM_PHRASES_NORM = {_strip_accents(p) for p in _STRONG_AFFIRM_PHRASES}
_AFFIRMATIVE_KEYWORDS_NORM = {_strip_accents(k) for k in _AFFIRMATIVE_KEYWORDS}
_QUERY_KEYWORDS_NORM = {_strip_accents(k) for k in _QUERY_KEYWORDS}
_ABORT_KEYWORDS_NORM = {_strip_accents(k) for k in _ABORT_KEYWORDS}


def _classify_intent(text: str) -> str:
    """Return one of: 'affirm', 'query', 'abort', 'other'."""
    lower = (text or "").lower().strip()
    if not lower:
        return "other"
    norm = _strip_accents(lower)

    # Strong creation phrases bypass the word-length gate — the user is
    # explicitly confirming they want this added to the calendar.
    if any(phrase in norm for phrase in _STRONG_AFFIRM_PHRASES_NORM):
        return "affirm"

    if len(lower.split()) > _MAX_CONFIRMATION_WORDS:
        return "other"

    # Priority order: abort > query > affirm.
    # Abort wins because "no" alone is an explicit rejection of both options.
    # Query wins over affirm because phrases like "solo ver" contain "ver"
    # and should not be ambiguous with affirm keywords.
    if any(kw in norm for kw in _ABORT_KEYWORDS_NORM):
        return "abort"
    if any(kw in norm for kw in _QUERY_KEYWORDS_NORM):
        return "query"
    if any(kw in norm for kw in _AFFIRMATIVE_KEYWORDS_NORM):
        return "affirm"
    return "other"


def handle_pending_event(inbound: InboundMessage, user: Optional[dict]) -> bool:
    """
    Pre-pipeline gate (sibling to handle_pending_expense).

    If the user has a pending_event waiting for a clarify reply, interpret the
    current message as:
      - affirm  → create the event, send confirmation + follow-up
      - query   → show today's events
      - abort   → send a short ack, drop the stash
      - other   → drop the stash and fall through to the normal pipeline

    Returns True if consumed, False to let the pipeline run.
    """
    if not user:
        return False

    phone = inbound.user_phone_number
    ctx = get_user_context(phone)
    pending = ctx.get("pending_event")
    if not pending:
        return False

    intent = _classify_intent(inbound.text or "")
    lang = (user.get("language") or "es").lower()

    if intent == "other":
        update_user_context(phone, "pending_event", None)
        return False

    if intent == "abort":
        update_user_context(phone, "pending_event", None)
        send_whatsapp_message(phone, _ABORT_ACK.get(lang, _ABORT_ACK["es"]))
        return True

    # affirm and query both need the refresh token
    encrypted = user.get("google_calendar_refresh_token")
    if not encrypted:
        update_user_context(phone, "pending_event", None)
        send_whatsapp_message(phone, _NOT_CONNECTED.get(lang, _NOT_CONNECTED["es"]))
        return True

    try:
        refresh_token = decrypt(encrypted)
    except Exception as exc:
        logger.exception("Decrypt calendar token failed for %s: %s", phone, exc)
        update_user_context(phone, "pending_event", None)
        send_whatsapp_message(phone, _NOT_CONNECTED.get(lang, _NOT_CONNECTED["es"]))
        return True

    user_for_formatter = {**user, "phone_number": phone}

    if intent == "affirm":
        try:
            start_dt = datetime.fromisoformat(pending["start"])
            duration = pending.get("duration_minutes") or 60
            end_dt = start_dt + timedelta(minutes=duration)
            tz_str = user.get("timezone") or "UTC"

            event = create_event_for_user(
                refresh_token,
                title=pending["title"],
                start_iso=start_dt.isoformat(),
                end_iso=end_dt.isoformat(),
                timezone_str=tz_str,
                location=pending.get("location"),
            )
        except Exception as exc:
            logger.exception("Pending event creation failed for %s: %s", phone, exc)
            send_whatsapp_message(phone, _CREATE_ERROR.get(lang, _CREATE_ERROR["es"]))
            update_user_context(phone, "pending_event", None)
            return True

        result = AgentResult(
            agent_name="CalendarAgent",
            success=True,
            data={
                "type": "calendar_create",
                "title": pending["title"],
                "start": start_dt.isoformat(),
                "location": pending.get("location"),
                "event_id": event.get("id"),
            },
        )
        reply = format_response(result, user_for_formatter)
        send_whatsapp_message(phone, reply)
        send_whatsapp_message(phone, _FOLLOW_UP_COPY.get(lang, _FOLLOW_UP_COPY["es"]))
        update_user_context(phone, "pending_event", None)
        return True

    # intent == "query"
    try:
        events_raw = get_today_events_for_user(refresh_token) or []
        events = normalize_events(events_raw) if events_raw else []
        update_user_context(phone, "today_events", events)
        update_user_context(phone, "last_intent", "calendar_query")

        result = AgentResult(
            agent_name="CalendarAgent",
            success=True,
            data={
                "type": "calendar_query",
                "event_count": len(events),
                "events": events,
                "summary": summarize_day(events),
                "detailed": format_events_detailed(events),
            },
        )
        reply = format_response(result, user_for_formatter)
        send_whatsapp_message(phone, reply)
    except Exception as exc:
        logger.exception("Pending event query failed for %s: %s", phone, exc)
        send_whatsapp_message(phone, _QUERY_ERROR.get(lang, _QUERY_ERROR["es"]))

    update_user_context(phone, "pending_event", None)
    return True
