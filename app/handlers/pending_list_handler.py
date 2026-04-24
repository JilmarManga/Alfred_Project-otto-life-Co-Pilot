import logging
import re
from typing import Optional

from app.db.user_context_store import get_user_context, update_user_context
from app.models.inbound_message import InboundMessage
from app.responder.response_formatter import format_response
from app.services.whatsapp_sender import send_whatsapp_message

logger = logging.getLogger(__name__)

# Mirrors pending_travel_handler's keyword sets; kept separate so list UX
# can diverge later without regressing travel.
_ABORT_KEYWORDS = {
    "no", "nah", "nop", "nope",
    "cancela", "cancelar", "déjalo", "dejalo", "olvida", "olvídalo", "olvidalo",
    "cancel", "nvm", "never mind", "nevermind",
}
_AFFIRMATIVE_KEYWORDS = {
    "sí", "si", "sii", "siii", "dale", "ok", "okay", "vale", "listo", "claro",
    "yes", "yep", "yeah", "sure", "please", "por favor",
    "confirma", "confirmo", "confirm",
}

_ABORT_ACK = {
    "es": "Listo 🙂",
    "en": "Got it 🙂",
}
_UNKNOWN_CHOICE_ACK = {
    "es": "No pude identificar esa lista 🤔 ¿Me la dices otra vez?",
    "en": "I couldn't identify that list 🤔 Can you say it again?",
}

# Matches pending_travel_handler's threshold — long replies mean the user
# moved on to a new topic.
_MAX_REPLY_WORDS = 6

# Ordinal vocabulary for the _choice step and for awaiting_disambiguation.
# We cap at three because the product caps lists at 3 per user.
_ORDINAL_WORDS = {
    0: {"1", "uno", "una", "primera", "primero", "first", "1st"},
    1: {"2", "dos", "segunda", "segundo", "second", "2nd"},
    2: {"3", "tres", "tercera", "tercero", "third", "3rd"},
}

# Keyword → candidate agent class name for awaiting_disambiguation.
# Applied only when the keyword resolves to one of the two stashed candidates.
_DISAMBIG_KEYWORDS = {
    "lista": "ListAgent", "list": "ListAgent",
    "guardar": "ListAgent", "save": "ListAgent",
    "gasto": "ExpenseAgent", "expense": "ExpenseAgent",
    "dinero": "ExpenseAgent", "tarjeta": "ExpenseAgent",
    "calendario": "CalendarAgent", "calendar": "CalendarAgent",
    "agenda": "CalendarAgent",
    "clima": "WeatherAgent", "weather": "WeatherAgent",
    "viaje": "TravelAgent", "travel": "TravelAgent", "salir": "TravelAgent",
    "resumen": "SummaryAgent", "summary": "SummaryAgent",
}


def _matches_keyword(text: str, keywords: set) -> bool:
    lower = text.lower().strip()
    for kw in keywords:
        if " " in kw:
            if kw in lower:
                return True
        else:
            if re.search(r"\b" + re.escape(kw) + r"\b", lower):
                return True
    return False


def _is_abort(text: str) -> bool:
    return _matches_keyword(text, _ABORT_KEYWORDS)


def _is_affirmative(text: str) -> bool:
    return _matches_keyword(text, _AFFIRMATIVE_KEYWORDS)


def _match_list_name(text: str, list_names: list) -> Optional[str]:
    """Return a list name from `list_names` if the user's reply matches one
    (case-insensitive, substring, or ordinal). None if nothing matches."""
    lower = text.lower().strip()
    if not lower:
        return None

    # Exact case-insensitive name match
    for name in list_names:
        if (name or "").lower().strip() == lower:
            return name

    # Substring: user typed the list name somewhere in a short reply
    for name in list_names:
        nl = (name or "").lower().strip()
        if nl and nl in lower:
            return name

    # Ordinal ("1", "first", "la primera", etc.)
    for idx, words in _ORDINAL_WORDS.items():
        if lower in words and idx < len(list_names):
            return list_names[idx]

    return None


def _resolve_disambiguation_choice(text: str, candidates: list) -> Optional[str]:
    """Map the user's short reply to one of the two candidate agents.
    Returns the candidate name or None if no match."""
    lower = text.lower().strip()
    if not lower or len(candidates) < 2:
        return None

    # Ordinal — "1"/"first" → candidates[0], "2"/"second" → candidates[1]
    for idx, words in _ORDINAL_WORDS.items():
        if lower in words and idx < len(candidates):
            return candidates[idx]

    # Keyword — only counts if it resolves to one of the two candidates
    for kw, agent_name in _DISAMBIG_KEYWORDS.items():
        if re.search(r"\b" + re.escape(kw) + r"\b", lower) and agent_name in candidates:
            return agent_name

    return None


def handle_pending_list(inbound: InboundMessage, user: Optional[dict]) -> bool:
    """Pre-pipeline gate for the three list-related multi-turn flows.

    Step `_choice`:
        User is picking which of their 2–3 existing lists to save into.
        On match: dispatches SaveToListSkill via run_skill and replies.

    Step `awaiting_delete_confirmation`:
        User is replying yes/no to a delete confirmation.
        On "sí"/"yes": dispatches ConfirmDeleteListSkill.

    Step `awaiting_disambiguation`:
        Router saw both a list op and a functional agent; user is picking one.
        ListAgent side → ListAgent().execute with the stashed ParsedMessage.
        Other side     → route(parsed, skip_list=True) then that agent's execute.

    Returns True if the message was consumed, False to let the normal pipeline run.
    """
    if not user:
        return False

    phone = inbound.user_phone_number
    ctx = get_user_context(phone)
    pending = ctx.get("pending_list")
    if not pending:
        return False

    step = pending.get("step")
    if step not in {"_choice", "awaiting_delete_confirmation", "awaiting_disambiguation"}:
        update_user_context(phone, "pending_list", None)
        return False

    text = (inbound.text or "").strip()
    lang = (user.get("language") or "es").lower()

    # Abort wins at every step.
    if _is_abort(text):
        update_user_context(phone, "pending_list", None)
        send_whatsapp_message(phone, _ABORT_ACK.get(lang, _ABORT_ACK["es"]))
        return True

    # Long replies → user moved on; drop stash and let the pipeline run.
    if len(text.split()) > _MAX_REPLY_WORDS:
        update_user_context(phone, "pending_list", None)
        return False

    if step == "_choice":
        return _handle_choice(phone, text, lang, user, pending)
    if step == "awaiting_delete_confirmation":
        return _handle_delete_confirmation(phone, text, lang, user, pending)
    return _handle_disambiguation(phone, text, lang, user, pending)


def _handle_choice(phone: str, text: str, lang: str, user: dict, pending: dict) -> bool:
    """User picks which list to save into."""
    # Imported inside the function to avoid a circular import at module load:
    # pending_list_handler -> ListAgent -> skills -> repositories -> ... -> handlers
    from app.agents.list_agent import ListAgent
    from app.agents.list_agent.skill_context import SkillContext

    list_names = pending.get("list_names") or []
    item = pending.get("item")
    label = pending.get("label")

    picked = _match_list_name(text, list_names)
    if picked is None:
        # Unmatched short reply — per plan, we consume with a warm error so the
        # save isn't silently dropped. The user can retry naming their list.
        update_user_context(phone, "pending_list", None)
        send_whatsapp_message(
            phone,
            _UNKNOWN_CHOICE_ACK.get(lang, _UNKNOWN_CHOICE_ACK["es"]),
        )
        return True

    user_with_phone = {**user, "phone_number": phone}
    ctx = SkillContext(
        user=user_with_phone,
        inbound_text=text,
        payload={
            "resolved_list_name": picked,
            "item": item,
            "label": label,
        },
    )
    result = ListAgent().run_skill("save_to_list", ctx)
    reply = format_response(result, user_with_phone)
    send_whatsapp_message(phone, reply)
    update_user_context(phone, "pending_list", None)
    return True


def _handle_delete_confirmation(phone: str, text: str, lang: str, user: dict, pending: dict) -> bool:
    """User replies yes/no to the delete confirmation."""
    if not _is_affirmative(text):
        # Not a confirmation → drop stash and let the pipeline process the message.
        update_user_context(phone, "pending_list", None)
        return False

    from app.agents.list_agent import ListAgent
    from app.agents.list_agent.skill_context import SkillContext

    list_id = pending.get("list_id")
    list_name = pending.get("list_name")

    user_with_phone = {**user, "phone_number": phone}
    ctx = SkillContext(
        user=user_with_phone,
        inbound_text=text,
        payload={"list_id": list_id, "list_name": list_name},
    )
    result = ListAgent().run_skill("confirm_delete_list", ctx)
    reply = format_response(result, user_with_phone)
    send_whatsapp_message(phone, reply)
    update_user_context(phone, "pending_list", None)
    return True


def _handle_disambiguation(phone: str, text: str, lang: str, user: dict, pending: dict) -> bool:
    """User picks which candidate agent they meant."""
    from app.agents.list_agent import ListAgent
    from app.router.deterministic_router import route

    candidates = pending.get("candidates") or []
    original_parsed = pending.get("original_parsed")
    if len(candidates) < 2 or original_parsed is None:
        update_user_context(phone, "pending_list", None)
        return False

    picked = _resolve_disambiguation_choice(text, candidates)
    if picked is None:
        # Ambiguous reply — drop stash and let the natural pipeline handle it.
        update_user_context(phone, "pending_list", None)
        return False

    user_with_phone = {**user, "phone_number": phone}

    if picked == "ListAgent":
        result = ListAgent().execute(original_parsed, user_with_phone)
    else:
        # Re-run the router with the list predicate disabled — returns the
        # original keyword agent cleanly, no string→class lookup.
        decision = route(original_parsed, skip_list=True)
        if decision.agent is None:
            update_user_context(phone, "pending_list", None)
            return False
        result = decision.agent.execute(original_parsed, user_with_phone)

    reply = format_response(result, user_with_phone)
    send_whatsapp_message(phone, reply)

    # Mirror the main webhook's follow_up handling.
    follow_up = (result.data or {}).get("follow_up_message")
    if follow_up:
        send_whatsapp_message(phone, follow_up)

    update_user_context(phone, "pending_list", None)
    return True
