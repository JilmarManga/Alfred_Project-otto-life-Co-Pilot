import logging
import re
from typing import Optional

from app.db.user_context_store import get_user_context, update_user_context
from app.models.inbound_message import InboundMessage
from app.responder.response_formatter import format_response
from app.services.whatsapp_sender import send_whatsapp_message

logger = logging.getLogger(__name__)

# Short replies that abort the travel flow at any step.
_ABORT_KEYWORDS = {
    "no", "nah", "nop", "nope",
    "cancela", "cancelar", "déjalo", "dejalo", "olvida", "olvídalo", "olvidalo",
    "cancel", "nvm", "never mind", "nevermind",
}

# Short replies that confirm the reminder offer (step=awaiting_reminder_confirmation).
_AFFIRMATIVE_KEYWORDS = {
    "sí", "si", "sii", "siii", "dale", "ok", "okay", "vale", "listo", "claro",
    "yes", "yep", "yeah", "sure", "please", "por favor",
}

_ABORT_ACK = {
    "es": "Listo 🙂",
    "en": "Got it 🙂",
}

_NOT_CONNECTED = {
    "es": "No pude conectar tu calendario. Termina la configuración 🙏",
    "en": "I couldn't reach your calendar. Please finish the setup 🙏",
}

# How many words a "confirmation" reply can have at most.
# Longer messages mean the user moved on to a new topic.
_MAX_REPLY_WORDS = 6


def _matches_keyword(text: str, keywords: set) -> bool:
    """Word-boundary match for single-word keywords; substring match for multi-word.
    Prevents false positives like 'no' matching inside 'andino'."""
    lower = text.lower().strip()
    for kw in keywords:
        if " " in kw:
            if kw in lower:
                return True
        else:
            if re.search(r'\b' + re.escape(kw) + r'\b', lower):
                return True
    return False


def _is_abort(text: str) -> bool:
    return _matches_keyword(text, _ABORT_KEYWORDS)


def _is_affirmative(text: str) -> bool:
    return _matches_keyword(text, _AFFIRMATIVE_KEYWORDS)


def handle_pending_travel(inbound: InboundMessage, user: Optional[dict]) -> bool:
    """Pre-pipeline gate for the two-step travel location / reminder flow.

    Step 1  (step='awaiting_location'):
        User's message is treated as a place name.
        On success: computes leave time, offers reminder, advances stash to step 2.
        On geocoding/maps failure: sends warm error copy, clears stash.
        On abort: acks, clears stash.
        On long message (user moved on): clears stash, returns False.

    Step 2  (step='awaiting_reminder_confirmation'):
        User is replying yes/no to the reminder offer.
        On affirm: schedules the reminder (Commit 3), sends confirmation.
        On abort: acks, clears stash.
        On other / long message: clears stash, returns False.

    Returns True if the message was consumed, False to let the normal pipeline run.
    """
    if not user:
        return False

    phone = inbound.user_phone_number
    ctx = get_user_context(phone)
    pending = ctx.get("pending_travel")
    if not pending:
        return False

    step = pending.get("step")
    if step not in ("awaiting_location", "awaiting_reminder_confirmation"):
        # Unexpected state — clear and let pipeline run.
        update_user_context(phone, "pending_travel", None)
        return False

    text = (inbound.text or "").strip()
    lang = (user.get("language") or "es").lower()

    # Abort wins at any step.
    if _is_abort(text):
        update_user_context(phone, "pending_travel", None)
        send_whatsapp_message(phone, _ABORT_ACK.get(lang, _ABORT_ACK["es"]))
        return True

    # Long messages mean the user moved on to a new topic.
    if len(text.split()) > _MAX_REPLY_WORDS:
        update_user_context(phone, "pending_travel", None)
        return False

    if step == "awaiting_location":
        return _handle_location_reply(phone, text, lang, user, pending)

    # step == "awaiting_reminder_confirmation"
    return _handle_reminder_reply(phone, text, lang, user, pending)


def _handle_location_reply(
    phone: str,
    text: str,
    lang: str,
    user: dict,
    pending: dict,
) -> bool:
    """User replied with a place name. Geocode + compute leave time."""
    # Import here to avoid circular imports at module load time.
    from app.agents.travel_agent import TravelAgent
    from app.agents.travel_agent.skill_context import SkillContext
    from app.models.agent_result import AgentResult

    user_with_phone = {**user, "phone_number": phone}
    ctx = SkillContext(
        user=user_with_phone,
        inbound_text=text,
        payload={"pending_travel": pending},
    )

    result: AgentResult = TravelAgent().run_skill("resolve_event_location", ctx)
    reply = format_response(result, user_with_phone)
    send_whatsapp_message(phone, reply)

    if result.success:
        # Advance the stash to step 2 so the user can confirm the reminder.
        data = result.data or {}
        update_user_context(phone, "pending_travel", {
            **pending,
            "step": "awaiting_reminder_confirmation",
            "resolved_location": data.get("location"),
            "leave_at_display": data.get("leave_at"),   # formatted string, e.g. "8:20 AM"
            "duration_minutes": data.get("duration_minutes"),
        })
    else:
        # Geocoding or Maps failed — clear the stash, the error copy was already sent.
        update_user_context(phone, "pending_travel", None)

    return True


def _handle_reminder_reply(
    phone: str,
    text: str,
    lang: str,
    user: dict,
    pending: dict,
) -> bool:
    """User is answering yes/no to the departure-reminder offer."""
    if _is_affirmative(text):
        from app.agents.travel_agent import TravelAgent
        from app.agents.travel_agent.skill_context import SkillContext

        user_with_phone = {**user, "phone_number": phone}
        ctx = SkillContext(
            user=user_with_phone,
            inbound_text=text,
            payload={"pending_travel": pending},
        )
        result = TravelAgent().run_skill("schedule_departure_reminder", ctx)
        reply = format_response(result, user_with_phone)
        send_whatsapp_message(phone, reply)
        update_user_context(phone, "pending_travel", None)
        return True

    # Unrecognized reply at step 2 → drop stash, run pipeline.
    update_user_context(phone, "pending_travel", None)
    return False
