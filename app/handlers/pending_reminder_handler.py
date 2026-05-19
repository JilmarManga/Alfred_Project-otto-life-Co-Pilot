import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.agents.reminder_agent._shared.time_resolver import fold, parse_reply_time
from app.db.user_context_store import get_user_context, update_user_context
from app.models.inbound_message import InboundMessage
from app.repositories.user_reminder_repository import UserReminderRepository
from app.responder.response_formatter import format_response
from app.services.whatsapp_sender import send_whatsapp_message

logger = logging.getLogger(__name__)

# Mirrors pending_list_handler's keyword sets / thresholds.
_ABORT_KEYWORDS = {
    "no", "nah", "nop", "nope",
    "cancela", "cancelar", "déjalo", "dejalo", "olvida", "olvídalo", "olvidalo",
    "cancel", "nvm", "never mind", "nevermind",
}
# Post-delivery "delete it" intent (abort ∪ explicit delete words).
_DELETE_KEYWORDS = _ABORT_KEYWORDS | {
    "delete", "delete it", "bórralo", "borralo", "elimínalo", "eliminalo",
    "elimina", "borra", "quítalo", "quitalo", "ya no", "remove it",
}
_IN_AN_HOUR_KEYWORDS = {
    "in an hour", "in 1 hour", "in one hour", "an hour",
    "en una hora", "una hora", "1 hora", "en 1 hora", "una horita", "1 hour",
}
_REMINDER_CHOICE_KEYWORDS = {
    "recordatorio", "recuérdame", "recuerdame", "recuérdamelo", "recuerdamelo",
    "reminder", "remind", "just remind", "solo recuérdame", "solo recuerdame",
    "el recordatorio",
}
_EVENT_CHOICE_KEYWORDS = {
    "calendario", "calendar", "agenda", "evento", "event",
    "al calendario", "a mi calendario", "add to my calendar",
    "cita", "appointment", "reunión", "reunion", "meeting",
}

_ABORT_ACK = {"es": "Listo, lo dejo así 🙂", "en": "Okay, leaving it 🙂"}
_REASK_TIME = {
    "es": "¿A qué hora? ¿O en la mañana / tarde / noche? ⏰",
    "en": "What time? Or morning / afternoon / night? ⏰",
}
_REASK_CHOICE = {
    "es": "¿Te lo recuerdo o lo agrego a tu calendario? 🐙",
    "en": "A reminder, or add it to your calendar? 🐙",
}
_REASK_CANCEL = {
    "es": "¿Cuál cancelo? Dime el número 🙂",
    "en": "Which one should I cancel? Tell me the number 🙂",
}

_MAX_REPLY_WORDS = 6

_ORDINAL_WORDS = {
    0: {"1", "uno", "una", "primera", "primero", "first", "1st"},
    1: {"2", "dos", "segunda", "segundo", "second", "2nd"},
    2: {"3", "tres", "tercera", "tercero", "third", "3rd"},
    3: {"4", "cuatro", "cuarta", "cuarto", "fourth", "4th"},
    4: {"5", "cinco", "quinta", "quinto", "fifth", "5th"},
}

_KNOWN_STEPS = {
    "awaiting_time_of_day",
    "awaiting_reminder_or_event",
    "awaiting_cancel_choice",
}


def _matches_keyword(text: str, keywords: set) -> bool:
    lower = text.lower().strip()
    for kw in keywords:
        if " " in kw:
            if kw in lower:
                return True
        elif re.search(r"\b" + re.escape(kw) + r"\b", lower):
            return True
    return False


def _is_abort(text: str) -> bool:
    return _matches_keyword(text, _ABORT_KEYWORDS)


def _parse_iso(raw):
    try:
        dt = datetime.fromisoformat(raw)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def handle_pending_reminder(inbound: InboundMessage, user: Optional[dict]) -> bool:
    """Pre-pipeline gate for ReminderAgent's two multi-turn flows.

    (a) In-memory pre-schedule clarify (`user_context_store` key
        `pending_reminder`): time-of-day, reminder-vs-event, cancel-choice.
    (b) Durable post-delivery follow-up: reads `user_reminders` docs in
        `awaiting_followup` (survives restarts; the reply can arrive minutes
        later on a different process). 10-min window enforced here AND by the
        cron sweep.

    Returns True if the message was consumed, False to let the pipeline run.
    """
    if not user:
        return False

    phone = inbound.user_phone_number
    text = (inbound.text or "").strip()
    lang = (user.get("language") or "es").lower()

    pending = get_user_context(phone).get("pending_reminder")
    if pending:
        step = pending.get("step")
        if step not in _KNOWN_STEPS:
            update_user_context(phone, "pending_reminder", None)
            return False
        return _handle_pre_schedule(phone, text, lang, user, pending, step)

    return _handle_post_delivery(phone, text, lang, user)


# ---------------------------------------------------------------------- #
# (a) In-memory pre-schedule clarify                                       #
# ---------------------------------------------------------------------- #

def _handle_pre_schedule(phone, text, lang, user, pending, step) -> bool:
    if _is_abort(text) and step != "awaiting_reminder_or_event":
        update_user_context(phone, "pending_reminder", None)
        send_whatsapp_message(phone, _ABORT_ACK.get(lang, _ABORT_ACK["es"]))
        return True

    if len(text.split()) > _MAX_REPLY_WORDS:
        update_user_context(phone, "pending_reminder", None)
        return False

    if step == "awaiting_time_of_day":
        return _handle_time_of_day(phone, text, lang, user, pending)
    if step == "awaiting_reminder_or_event":
        return _handle_reminder_or_event(phone, text, lang, user, pending)
    return _handle_cancel_choice(phone, text, lang, user, pending)


def _handle_time_of_day(phone, text, lang, user, pending) -> bool:
    from app.agents.reminder_agent import ReminderAgent
    from app.agents.reminder_agent.skill_context import SkillContext

    tz_name = (user.get("timezone") or "UTC")
    now_utc = datetime.now(timezone.utc)
    base_date_iso = pending.get("reminder_time")  # date carrier (may be None)

    rt_iso, rt_period = parse_reply_time(
        text, base_date_iso=base_date_iso, tz_name=tz_name, now_utc=now_utc,
    )
    if rt_iso is None and rt_period is None:
        # Keep the stash so the user can answer again — text isn't lost.
        send_whatsapp_message(phone, _REASK_TIME.get(lang, _REASK_TIME["es"]))
        return True

    if rt_period is not None:
        payload = {
            "reminder_text": pending.get("reminder_text"),
            "reminder_time": base_date_iso,
            "reminder_period": rt_period,
            "force_set": True,
        }
    else:
        payload = {
            "reminder_text": pending.get("reminder_text"),
            "reminder_time": rt_iso,
            "reminder_period": None,
            "force_set": True,
        }

    user_with_phone = {**user, "phone_number": phone}
    ctx = SkillContext(user=user_with_phone, inbound_text=text, payload=payload)
    result = ReminderAgent().run_skill("set_reminder", ctx)
    send_whatsapp_message(phone, format_response(result, user_with_phone))
    update_user_context(phone, "pending_reminder", None)
    return True


def _handle_reminder_or_event(phone, text, lang, user, pending) -> bool:
    from app.agents.reminder_agent import ReminderAgent
    from app.agents.reminder_agent.skill_context import SkillContext

    user_with_phone = {**user, "phone_number": phone}
    original_parsed = pending.get("original_parsed")

    wants_event = _matches_keyword(text, _EVENT_CHOICE_KEYWORDS)
    wants_reminder = _matches_keyword(text, _REMINDER_CHOICE_KEYWORDS)
    is_abort = _is_abort(text)

    # Abort here means "neither" — drop it.
    if is_abort and not wants_event and not wants_reminder:
        update_user_context(phone, "pending_reminder", None)
        send_whatsapp_message(phone, _ABORT_ACK.get(lang, _ABORT_ACK["es"]))
        return True

    if wants_event and not wants_reminder:
        from app.agents.calendar_agent import CalendarAgent

        update_user_context(phone, "pending_reminder", None)
        if original_parsed is None:
            return False
        result = CalendarAgent().execute(original_parsed, user_with_phone)
        send_whatsapp_message(phone, format_response(result, user_with_phone))
        follow_up = (result.data or {}).get("follow_up_message")
        if follow_up:
            send_whatsapp_message(phone, follow_up)
        return True

    if wants_reminder and not wants_event:
        payload = {"force_set": True}
        if original_parsed is not None:
            payload.update({
                "reminder_text": original_parsed.reminder_text or pending.get("reminder_text"),
                "reminder_time": original_parsed.reminder_time,
                "reminder_period": original_parsed.reminder_period,
            })
        else:
            payload["reminder_text"] = pending.get("reminder_text")
        ctx = SkillContext(
            user=user_with_phone, parsed=original_parsed,
            inbound_text=text, payload=payload,
        )
        result = ReminderAgent().run_skill("set_reminder", ctx)
        send_whatsapp_message(phone, format_response(result, user_with_phone))
        update_user_context(phone, "pending_reminder", None)
        return True

    # Unclear — re-ask, keep the stash.
    send_whatsapp_message(phone, _REASK_CHOICE.get(lang, _REASK_CHOICE["es"]))
    return True


def _handle_cancel_choice(phone, text, lang, user, pending) -> bool:
    from app.agents.reminder_agent import ReminderAgent
    from app.agents.reminder_agent.skill_context import SkillContext

    candidates = pending.get("candidates") or []
    lower = text.lower().strip()
    picked = None

    for idx, words in _ORDINAL_WORDS.items():
        if lower in words and idx < len(candidates):
            picked = candidates[idx]
            break
    if picked is None:
        folded = fold(text)
        for cand in candidates:
            ct = fold(cand.get("reminder_text"))
            if ct and (ct in folded or folded in ct):
                picked = cand
                break

    if picked is None:
        send_whatsapp_message(phone, _REASK_CANCEL.get(lang, _REASK_CANCEL["es"]))
        return True

    user_with_phone = {**user, "phone_number": phone}
    ctx = SkillContext(
        user=user_with_phone,
        inbound_text=text,
        payload={
            "resolved_doc_id": picked.get("id"),
            "reminder_text": picked.get("reminder_text"),
        },
    )
    result = ReminderAgent().run_skill("cancel_reminder", ctx)
    send_whatsapp_message(phone, format_response(result, user_with_phone))
    update_user_context(phone, "pending_reminder", None)
    return True


# ---------------------------------------------------------------------- #
# (b) Durable post-delivery follow-up                                      #
# ---------------------------------------------------------------------- #

def _handle_post_delivery(phone, text, lang, user) -> bool:
    docs = UserReminderRepository.list_awaiting_followup_for_phone(phone)
    if not docs:
        return False

    docs.sort(key=lambda d: d.get("delivered_at") or "", reverse=True)
    doc = docs[0]
    doc_id = doc.get("id")

    now_utc = datetime.now(timezone.utc)
    delivered = _parse_iso(doc.get("delivered_at"))

    # 10-min enforcement: a late reply does NOT count. Drop the doc and let
    # the pipeline answer the message as a fresh request.
    if delivered is None or (now_utc - delivered) > timedelta(minutes=10):
        UserReminderRepository.delete(doc_id)
        return False

    tz_name = doc.get("tz") or user.get("timezone") or "UTC"
    reminder_text = doc.get("reminder_text") or ""

    # Precedence: delete > in_an_hour > new_time > unrelated.
    if _matches_keyword(text, _DELETE_KEYWORDS):
        mode, rt_iso, rt_period = "delete", None, None
    elif _matches_keyword(text, _IN_AN_HOUR_KEYWORDS):
        mode, rt_iso, rt_period = "in_an_hour", None, None
    else:
        rt_iso, rt_period = parse_reply_time(
            text, base_date_iso=None, tz_name=tz_name, now_utc=now_utc,
        )
        if (rt_iso or rt_period) and len(text.split()) <= _MAX_REPLY_WORDS:
            mode = "new_time"
        else:
            # Unrelated / moved on → spec: delete the reminder, pipeline answers.
            UserReminderRepository.delete(doc_id)
            return False

    from app.agents.reminder_agent import ReminderAgent
    from app.agents.reminder_agent.skill_context import SkillContext

    user_with_phone = {**user, "phone_number": phone}
    ctx = SkillContext(
        user=user_with_phone,
        inbound_text=text,
        payload={
            "doc_id": doc_id,
            "mode": mode,
            "reminder_text": reminder_text,
            "tz": tz_name,
            "reminder_time": rt_iso,
            "reminder_period": rt_period,
        },
    )
    result = ReminderAgent().run_skill("reschedule_reminder", ctx)
    send_whatsapp_message(phone, format_response(result, user_with_phone))
    return True
