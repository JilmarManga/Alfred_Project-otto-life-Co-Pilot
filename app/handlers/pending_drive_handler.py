"""Pre-pipeline gate for the two Drive multi-turn flows.

Step `awaiting_file_choice`:
    The user's file name matched several Drive files. They pick one; we
    re-dispatch the ORIGINAL intent (read / analyze / modify) against the
    chosen file via run_skill.

Step `awaiting_modify_confirmation`:
    A modification was staged by propose_modification. NOTHING has been
    written yet. Only an explicit affirmative dispatches
    apply_modification. Anything else aborts the change. This is the
    user-authorization gate the whole feature is built around.

Mirrors pending_list_handler's shape (abort wins, long replies fall through,
ordinal/name matching). No domain logic lives here.
"""
import logging
import re
from datetime import datetime, timezone
from typing import Optional

from app.db.user_context_store import get_user_context, update_user_context
from app.models.inbound_message import InboundMessage
from app.responder.response_formatter import format_response
from app.services.whatsapp_sender import send_whatsapp_message

logger = logging.getLogger(__name__)

_ABORT_KEYWORDS = {
    "no", "nah", "nop", "nope",
    "cancela", "cancelar", "déjalo", "dejalo", "olvida", "olvídalo", "olvidalo",
    "cancel", "nvm", "never mind", "nevermind", "stop", "para",
}
_AFFIRMATIVE_KEYWORDS = {
    "sí", "si", "sii", "siii", "dale", "ok", "okay", "vale", "listo", "claro",
    "yes", "yep", "yeah", "sure", "please", "por favor", "hazlo", "do it",
    "confirma", "confirmo", "confirm", "adelante", "go ahead",
}

_ABORT_ACK = {
    "es": "Listo, no cambié nada 🙂",
    "en": "Got it — I didn't change anything 🙂",
}
_UNKNOWN_CHOICE_ACK = {
    "es": "No pude identificar ese archivo 🤔 ¿Me dices el nombre otra vez?",
    "en": "I couldn't identify that file 🤔 Can you tell me the name again?",
}
_STALE_ACK = {
    "es": "Ese cambio expiró por seguridad. Pídemelo de nuevo y te muestro la vista previa 🙂",
    "en": "That change expired for safety. Ask me again and I'll show you a fresh preview 🙂",
}

_MAX_REPLY_WORDS = 8
# A staged change is only valid briefly — forces a fresh preview (and a fresh
# revision check) if the user dawdles.
_PENDING_TTL_SECONDS = 600

_ORDINAL_WORDS = {
    0: {"1", "uno", "una", "primera", "primero", "first", "1st"},
    1: {"2", "dos", "segunda", "segundo", "second", "2nd"},
    2: {"3", "tres", "tercera", "tercero", "third", "3rd"},
    3: {"4", "cuatro", "cuarta", "cuarto", "fourth", "4th"},
    4: {"5", "cinco", "quinta", "quinto", "fifth", "5th"},
}

_INTENT_TO_SKILL = {
    "read": "read_file",
    "analyze": "analyze_file",
    "find": "find_file",
    "modify": "propose_modification",
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


def _is_affirmative(text: str) -> bool:
    return _matches_keyword(text, _AFFIRMATIVE_KEYWORDS)


def _match_candidate(text: str, candidates: list) -> Optional[dict]:
    """Return a candidate file dict by exact/substring name or ordinal."""
    lower = text.lower().strip()
    if not lower:
        return None
    for c in candidates:
        if (c.get("name") or "").lower().strip() == lower:
            return c
    for c in candidates:
        nl = (c.get("name") or "").lower().strip()
        if nl and nl in lower:
            return c
    for idx, words in _ORDINAL_WORDS.items():
        if lower in words and idx < len(candidates):
            return candidates[idx]
    return None


def _is_stale(pending: dict) -> bool:
    created = pending.get("created_at")
    if not created:
        return False
    try:
        created_dt = datetime.fromisoformat(created)
    except (ValueError, TypeError):
        return False
    if created_dt.tzinfo is None:
        created_dt = created_dt.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - created_dt).total_seconds()
    return age > _PENDING_TTL_SECONDS


def handle_pending_drive(inbound: InboundMessage, user: Optional[dict]) -> bool:
    """Returns True if the message was consumed, False to let the pipeline run."""
    if not user:
        return False

    phone = inbound.user_phone_number
    ctx = get_user_context(phone)
    pending = ctx.get("pending_drive")
    if not pending:
        return False

    step = pending.get("step")
    if step not in {"awaiting_file_choice", "awaiting_modify_confirmation"}:
        update_user_context(phone, "pending_drive", None)
        return False

    text = (inbound.text or "").strip()
    lang = (user.get("language") or "es").lower()

    # Abort wins everywhere — and explicitly tells the user nothing changed.
    if _is_abort(text):
        update_user_context(phone, "pending_drive", None)
        send_whatsapp_message(phone, _ABORT_ACK.get(lang, _ABORT_ACK["es"]))
        return True

    # Expired stage → never apply a stale change; force a fresh preview.
    if _is_stale(pending):
        update_user_context(phone, "pending_drive", None)
        send_whatsapp_message(phone, _STALE_ACK.get(lang, _STALE_ACK["es"]))
        return True

    # Long reply → user moved on; drop the stash, let the pipeline run.
    if len(text.split()) > _MAX_REPLY_WORDS:
        update_user_context(phone, "pending_drive", None)
        return False

    if step == "awaiting_file_choice":
        return _handle_file_choice(phone, text, lang, user, pending)
    return _handle_modify_confirmation(phone, text, lang, user, pending)


def _handle_file_choice(phone: str, text: str, lang: str, user: dict, pending: dict) -> bool:
    from app.agents.drive_agent import DriveAgent
    from app.agents.drive_agent.skill_context import SkillContext

    candidates = pending.get("candidates") or []
    intent = pending.get("intent") or "read"
    skill = _INTENT_TO_SKILL.get(intent, "read_file")

    picked = _match_candidate(text, candidates)
    if picked is None:
        update_user_context(phone, "pending_drive", None)
        send_whatsapp_message(
            phone, _UNKNOWN_CHOICE_ACK.get(lang, _UNKNOWN_CHOICE_ACK["es"]),
        )
        return True

    user_with_phone = {**user, "phone_number": phone}
    payload = {"file_ref": picked.get("name")}
    if intent == "modify" and pending.get("edit_spec"):
        payload["edit_spec"] = pending["edit_spec"]

    # Clear the choice stash before dispatch; propose_modification will set its
    # own awaiting_modify_confirmation stash if it produces a preview.
    update_user_context(phone, "pending_drive", None)

    result = DriveAgent().run_skill(
        skill,
        SkillContext(user=user_with_phone, inbound_text=text, payload=payload),
    )
    reply = format_response(result, user_with_phone)
    if reply:
        send_whatsapp_message(phone, reply)
    return True


def _handle_modify_confirmation(phone: str, text: str, lang: str, user: dict, pending: dict) -> bool:
    if not _is_affirmative(text):
        # Not a clear yes and not an abort → safest is to NOT apply. Drop the
        # stash and let the pipeline handle whatever they actually said.
        update_user_context(phone, "pending_drive", None)
        return False

    from app.agents.drive_agent import DriveAgent
    from app.agents.drive_agent.skill_context import SkillContext

    user_with_phone = {**user, "phone_number": phone}
    result = DriveAgent().run_skill(
        "apply_modification",
        SkillContext(user=user_with_phone, inbound_text=text, payload=pending),
    )
    reply = format_response(result, user_with_phone)
    if reply:
        send_whatsapp_message(phone, reply)
    update_user_context(phone, "pending_drive", None)
    return True
