"""Sends the Google Drive OAuth connect/reconnect link.

Mirrors `calendar_reconnect.py` but operates entirely on the isolated
`google_drive_oauth_*` state namespace and the `google_drive_*` credential
fields. It never touches calendar state, onboarding_state, or
connected_accounts — connecting Drive is independent of calendar.

Used in two situations:
  1. A user asks Otto to do something with Drive but hasn't connected yet.
  2. The stored Drive token is rejected by Google (revoked / expired) and the
     dead token must be cleared and a fresh link sent.
"""
import logging
import os
import secrets
from datetime import datetime, timedelta

from app.repositories.user_repository import UserRepository
from app.services.whatsapp_sender import send_whatsapp_message

logger = logging.getLogger(__name__)

_CONNECT_COPY = {
    "es": "Para trabajar con tus archivos de Drive, conéctalo aquí 👉 {link}\n\nSiempre te mostraré el cambio y esperaré tu *sí* antes de editar nada.",
    "en": "To work with your Drive files, connect it here 👉 {link}\n\nI'll always show you the change and wait for your *yes* before editing anything.",
}
_RECONNECT_COPY = {
    "es": "Tu conexión con Google Drive caducó 😕. Vuelve a conectarla aquí 👉 {link}",
    "en": "Your Google Drive connection expired 😕. Reconnect here 👉 {link}",
}


def _build_authorize_url(state_token: str) -> str:
    base = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
    return f"{base}/auth/google-drive/authorize?state={state_token}"


def _mint_and_send(phone: str, lang: str, copy_map: dict) -> None:
    state_token = secrets.token_urlsafe(32)
    expires_at = datetime.utcnow() + timedelta(hours=1)
    UserRepository.set_drive_oauth_state_token(phone, state_token, expires_at)
    link = _build_authorize_url(state_token)
    copy = copy_map.get((lang or "es").lower(), copy_map["es"])
    send_whatsapp_message(phone, copy.format(link=link))


def send_connect_link(phone: str, lang: str = "es") -> None:
    """First-time Drive connection prompt. Best-effort — failures are logged,
    never raised, so the caller's flow doesn't cascade-fail."""
    try:
        _mint_and_send(phone, lang, _CONNECT_COPY)
    except Exception as exc:
        logger.exception("Drive connect link dispatch failed for %s: %s", phone, exc)


def handle_drive_token_invalid(phone: str, lang: str = "es") -> None:
    """Clear the dead Drive token and send a fresh reconnect link.
    Best-effort: any exception is swallowed."""
    try:
        UserRepository.clear_drive_credentials(phone)
        _mint_and_send(phone, lang, _RECONNECT_COPY)
    except Exception as exc:
        logger.exception("Drive reconnect dispatch failed for %s: %s", phone, exc)
