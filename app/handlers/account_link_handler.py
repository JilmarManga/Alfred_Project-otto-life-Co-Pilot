"""Pre-pipeline gate: connect a SECOND calendar account.

A user past onboarding can write naturally ("quiero agregar mi segundo correo"
/ "add my second email"). Otto asks which provider, then sends the correct
OAuth link. Max 2 accounts, any provider mix.

Not an agent: this is OAuth orchestration with no business logic — same role
as the onboarding/reconnect link senders (Hard Rule #5: new pre-pipeline
concern → its own handler gate). Wired after `handle_pending_list`.

Deterministic, no LLM. Phrase matching is accent-insensitive substring,
mirroring `provider_detect` / `pending_event_handler._strip_accents`.
"""
import logging
import os
import secrets
import unicodedata
from datetime import datetime, timedelta
from typing import Optional

from app.db.user_context_store import get_user_context, update_user_context
from app.models.inbound_message import InboundMessage
from app.repositories.user_repository import UserRepository
from app.services.provider_detect import detect_provider
from app.services.whatsapp_sender import send_whatsapp_message

logger = logging.getLogger(__name__)


def _strip_accents(s: str) -> str:
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


# Multi-word phrases people use to add another account. Substring-matched.
_ADD_ACCOUNT_TRIGGERS = {
    # Spanish
    "agregar mi segundo correo", "agregar otro correo", "agregar otro mail",
    "anadir otro correo", "anadir otra cuenta", "agregar otra cuenta",
    "conectar otro correo", "conectar otra cuenta", "vincular otra cuenta",
    "agregar segundo correo", "segundo correo", "otra cuenta de correo",
    # English
    "add my second email", "add my second account", "add another email",
    "add another account", "add another mail", "connect another account",
    "connect another email", "link another account", "second email account",
}
_ADD_ACCOUNT_TRIGGERS_NORM = {_strip_accents(t) for t in _ADD_ACCOUNT_TRIGGERS}

_ABORT_NORM = {_strip_accents(k) for k in {
    "no", "cancela", "cancelar", "dejalo", "olvidalo",
    "cancel", "nvm", "never mind", "nevermind", "stop",
}}

_COPY = {
    "ask_provider": {
        "en": "Sure — is the second account *Gmail* or *Outlook*?",
        "es": "Claro — ¿la segunda cuenta es *Gmail* u *Outlook*?",
    },
    "provider_retry": {
        "en": "Just reply *Gmail* or *Outlook* and I'll send the right link 🙂",
        "es": "Responde *Gmail* u *Outlook* y te envío el enlace correcto 🙂",
    },
    "link_sent": {
        "en": "Connect your {provider_name} calendar here 👇\n{link}\n\nI'll merge events from both accounts.",
        "es": "Conecta tu calendario de {provider_name} aquí 👇\n{link}\n\nCombinaré los eventos de ambas cuentas.",
    },
    "limit_reached": {
        "en": "You already have 2 calendars connected — that's the max for now 🙂",
        "es": "Ya tienes 2 calendarios conectados — es el máximo por ahora 🙂",
    },
    "abort_ack": {
        "en": "No problem 🙂",
        "es": "Sin problema 🙂",
    },
}

_PROVIDER_NAME = {"google": "Google", "microsoft": "Outlook"}


def _copy(key: str, lang: str, **kw) -> str:
    entry = _COPY[key]
    template = entry.get(lang) or entry["en"]
    return template.format(**kw) if kw else template


def _build_authorize_url(state_token: str, provider: str) -> str:
    base = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
    return f"{base}/auth/{provider}/authorize?state={state_token}"


def _is_abort(text: str) -> bool:
    norm = _strip_accents(text.lower().strip())
    return norm in _ABORT_NORM


def _matches_add_account(text: str) -> bool:
    norm = _strip_accents(text.lower().strip())
    return any(t in norm for t in _ADD_ACCOUNT_TRIGGERS_NORM)


def _send_second_account_link(phone: str, user: dict, provider: str) -> None:
    """Mint a fresh state token bound to provider+secondary slot, send link.
    Best-effort: Azure/env misconfig is logged, user gets a soft retry line."""
    lang = (user.get("language") or "en").lower()
    try:
        state_token = secrets.token_urlsafe(32)
        expires_at = datetime.utcnow() + timedelta(hours=1)
        UserRepository.set_oauth_state_token(
            phone, state_token, expires_at, provider=provider, slot="secondary"
        )
        link = _build_authorize_url(state_token, provider)
        send_whatsapp_message(
            phone,
            _copy("link_sent", lang, link=link,
                  provider_name=_PROVIDER_NAME.get(provider, "Google")),
        )
    except Exception as exc:
        logger.exception("Second-account link dispatch failed for %s: %s", phone, exc)
        send_whatsapp_message(
            phone,
            "I couldn't start that just now — try again in a bit 🙂"
            if lang == "en"
            else "No pude iniciar eso ahora — inténtalo de nuevo en un momento 🙂",
        )


def handle_account_link(inbound: InboundMessage, user: Optional[dict]) -> bool:
    """Returns True if the message was consumed by the add-account flow."""
    if user is None:
        return False

    phone = inbound.user_phone_number
    text = (inbound.text or "").strip()
    if not text:
        return False

    lang = (user.get("language") or "en").lower()
    ctx = get_user_context(phone)
    pending = ctx.get("pending_account_link")

    if pending:
        if pending.get("step") != "awaiting_provider":
            update_user_context(phone, "pending_account_link", None)
            return False
        if _is_abort(text):
            update_user_context(phone, "pending_account_link", None)
            send_whatsapp_message(phone, _copy("abort_ack", lang))
            return True
        provider = detect_provider(text)
        if provider is None:
            send_whatsapp_message(phone, _copy("provider_retry", lang))
            return True
        update_user_context(phone, "pending_account_link", None)
        _send_second_account_link(phone, user, provider)
        return True

    # No pending step — only act on a clear add-account request, and only
    # once the user has finished onboarding.
    state = user.get("onboarding_state")
    completed = state == "completed" or (
        user.get("onboarding_completed") and not state
    )
    if not completed:
        return False
    if not _matches_add_account(text):
        return False

    if UserRepository.count_connected_accounts(user) >= UserRepository.MAX_CONNECTED_ACCOUNTS:
        send_whatsapp_message(phone, _copy("limit_reached", lang))
        return True

    update_user_context(phone, "pending_account_link", {"step": "awaiting_provider"})
    send_whatsapp_message(phone, _copy("ask_provider", lang))
    return True
