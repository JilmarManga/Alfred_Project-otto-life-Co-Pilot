import logging

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.repositories.user_repository import UserRepository
from app.services import google_oauth
from app.services.google_calendar import get_today_events_for_user
from app.services.token_crypto import encrypt, TokenCryptoError
from app.services.whatsapp_sender import send_whatsapp_message

logger = logging.getLogger(__name__)

router = APIRouter()


_EXPIRED_PAGE = """
<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Link expired</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>body{font-family:-apple-system,Segoe UI,sans-serif;max-width:480px;margin:80px auto;padding:0 24px;color:#222;text-align:center}</style>
</head><body>
<h2>This link has expired</h2>
<p>Text Otto again to get a fresh one.</p>
</body></html>
"""

_DONE_PAGE = """
<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>All done</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>body{font-family:-apple-system,Segoe UI,sans-serif;max-width:480px;margin:80px auto;padding:0 24px;color:#222;text-align:center}</style>
</head><body>
<h2>All done — go back to WhatsApp</h2>
</body></html>
"""


@router.get("/auth/google/authorize")
async def authorize(state: str = ""):
    """
    Redirect the user to Google's OAuth consent screen.
    The `state` param is the opaque token we stored during onboarding.
    """
    if not state:
        return HTMLResponse(_EXPIRED_PAGE, status_code=400)

    user = UserRepository.get_user_by_oauth_state(state)
    if not user:
        return HTMLResponse(_EXPIRED_PAGE, status_code=400)

    try:
        url, code_verifier = google_oauth.build_authorize_url(state_token=state)
    except Exception as exc:
        logger.exception("Failed to build Google authorize URL: %s", exc)
        return HTMLResponse(_EXPIRED_PAGE, status_code=500)

    if code_verifier:
        UserRepository.set_oauth_state_token(
            user["phone"], state,
            user.get("google_oauth_state_expires_at"),
            code_verifier=code_verifier,
        )

    return RedirectResponse(url)


@router.get("/auth/google/callback")
async def callback(request: Request):
    """
    Google redirects here after user consent.
    Exchange code → refresh_token, encrypt, save, fetch today's events,
    send WhatsApp confirmation, redirect to /auth/done.
    """
    params = dict(request.query_params)
    code = params.get("code")
    state = params.get("state")
    error = params.get("error")

    if error or not code or not state:
        logger.warning("OAuth callback missing code/state or has error: %s", params)
        return HTMLResponse(_EXPIRED_PAGE, status_code=400)

    user = UserRepository.get_user_by_oauth_state(state)
    if not user:
        logger.warning("OAuth callback: no user for state token (expired or unknown)")
        return HTMLResponse(_EXPIRED_PAGE, status_code=400)

    phone = user["phone"]

    try:
        code_verifier = user.get("google_oauth_code_verifier")
        refresh_token = google_oauth.exchange_code(code=code, state_token=state, code_verifier=code_verifier)
    except Exception as exc:
        logger.exception("OAuth code exchange failed for %s: %s", phone, exc)
        send_whatsapp_message(
            phone,
            "I couldn't connect your calendar just now — please try the link again.",
        )
        return HTMLResponse(_EXPIRED_PAGE, status_code=500)

    try:
        encrypted = encrypt(refresh_token)
    except TokenCryptoError as exc:
        logger.exception("Token encryption failed for %s: %s", phone, exc)
        return HTMLResponse(_EXPIRED_PAGE, status_code=500)

    UserRepository.save_calendar_credentials(phone, encrypted)
    UserRepository.clear_oauth_state(phone)
    UserRepository.set_onboarding_state(phone, "completed")

    try:
        events = get_today_events_for_user(refresh_token)
        event_count = len(events)
        language = (user.get("language") or "en").lower()

        if event_count == 0:
            msg = (
                "Calendar connected ✅\n\nNothing on your calendar today — enjoy it 🙂\n\nYour first briefing arrives tomorrow morning. Ask me anything in the meantime."
                if language == "en"
                else "Calendario conectado ✅\n\nNo tienes nada hoy — disfrútalo 🙂\n\nTu primer resumen llega mañana por la mañana. Mientras tanto, pregúntame lo que quieras."
            )
        else:
            msg = (
                f"Calendar connected ✅\n\nYou have {event_count} event(s) today.\nYour first briefing arrives tomorrow morning.\n\nUntil then, ask me anything."
                if language == "en"
                else f"Calendario conectado ✅\n\nTienes {event_count} evento(s) hoy.\nTu primer resumen llega mañana por la mañana.\n\nMientras tanto, pregúntame lo que quieras."
            )
        send_whatsapp_message(phone, msg)
    except Exception as exc:
        logger.exception("Post-callback event fetch failed for %s: %s", phone, exc)
        send_whatsapp_message(
            phone,
            "Calendar connected ✅ — I'll have your briefing ready tomorrow morning.",
        )

    return RedirectResponse(url="/auth/done")


@router.get("/auth/done")
async def done():
    return HTMLResponse(_DONE_PAGE)
