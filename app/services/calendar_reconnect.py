"""Centralizes the 'Google rejected our refresh token → re-prompt OAuth' flow.

Triggered by `CalendarTokenInvalid` (raised in `google_calendar.py` when
`creds.refresh()` returns invalid_grant). Without this path the user just
sees a generic error and the dead token stays in Firestore — every
subsequent calendar action fails the same way.

We:
  1. Clear the dead refresh token so callers can no longer retry it.
  2. Mint a fresh OAuth state token (1h TTL, one-time-use).
  3. Reset onboarding_state to 'oauth_pending' so the onboarding gate
     re-surfaces the link if the user sends another calendar message.
  4. DM the user a per-language reconnect message with the link.

The PKCE verifier is generated at click-time inside the `/auth/google/
authorize` endpoint, so we do not store one here.
"""
import logging
import os
import secrets
from datetime import datetime, timedelta

from app.repositories.user_repository import UserRepository
from app.services.whatsapp_sender import send_whatsapp_message

logger = logging.getLogger(__name__)

_RECONNECT_COPY = {
    "es": "Tu conexión con Google Calendar caducó 😕. Vuelve a conectarla aquí 👉 {link}",
    "en": "Your Google Calendar connection expired 😕. Reconnect here 👉 {link}",
}


def _build_authorize_url(state_token: str) -> str:
    base = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
    return f"{base}/auth/google/authorize?state={state_token}"


def handle_token_invalid(phone: str, lang: str = "es") -> None:
    """Clear the dead token and send a fresh OAuth link. Best-effort: any
    exception is swallowed so callers can still send their own follow-up
    message without a cascading failure."""
    try:
        UserRepository.clear_calendar_credentials(phone)
        state_token = secrets.token_urlsafe(32)
        expires_at = datetime.utcnow() + timedelta(hours=1)
        UserRepository.set_oauth_state_token(phone, state_token, expires_at)
        UserRepository.set_onboarding_state(phone, "oauth_pending")
        link = _build_authorize_url(state_token)
        copy = _RECONNECT_COPY.get((lang or "es").lower(), _RECONNECT_COPY["es"])
        send_whatsapp_message(phone, copy.format(link=link))
    except Exception as exc:
        logger.exception("Reconnect dispatch failed for %s: %s", phone, exc)
