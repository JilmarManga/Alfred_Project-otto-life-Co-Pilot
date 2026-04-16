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


_PAGE_STYLE = """
<style>
  *{margin:0;padding:0;box-sizing:border-box}
  body{font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
       background:#f5f7fa;color:#1a1a2e;min-height:100vh;
       display:flex;align-items:center;justify-content:center;padding:24px}
  .card{background:#fff;border-radius:16px;box-shadow:0 4px 24px rgba(0,0,0,.08);
        max-width:420px;width:100%;padding:48px 32px;text-align:center}
  .icon{font-size:56px;margin-bottom:16px}
  /* Replace this div with <img src="..." alt="Otto" class="logo"> when logo is ready */
  .brand{font-size:18px;font-weight:600;color:#6c6c80;letter-spacing:.5px;margin-bottom:24px}
  .brand span{font-size:22px;vertical-align:middle}
  h1{font-size:24px;font-weight:700;color:#1a1a2e;margin-bottom:8px}
  .sub{font-size:15px;color:#6c6c80;line-height:1.5;margin-bottom:32px}
  .cta{display:inline-block;background:#25D366;color:#fff;font-size:15px;font-weight:600;
       text-decoration:none;padding:12px 28px;border-radius:28px;transition:background .2s}
  .cta:hover{background:#1ebe5a}
  .cta-muted{display:inline-block;color:#6c6c80;font-size:14px;margin-top:16px}
  .accent-green .icon{color:#4CAF50}
  .accent-amber .icon{color:#F5A623}
</style>
"""

_EXPIRED_PAGE = f"""
<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Link expired — Otto</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
{_PAGE_STYLE}
</head><body>
<div class="card accent-amber">
  <div class="icon">⚠️</div>
  <div class="brand">Otto <span>🐙</span></div>
  <h1>This link has expired</h1>
  <p class="sub">No worries — just send Otto a message on WhatsApp to get a fresh one.</p>
  <a class="cta" href="https://wa.me/" target="_blank">Open WhatsApp</a>
</div>
</body></html>
"""

_DONE_PAGE = f"""
<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>You're all set — Otto</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
{_PAGE_STYLE}
</head><body>
<div class="card accent-green">
  <div class="icon">✅</div>
  <div class="brand">Otto <span>🐙</span></div>
  <h1>You're all set!</h1>
  <p class="sub">Your calendar is connected. Otto will take it from here — your first briefing arrives tomorrow morning.</p>
  <a class="cta" href="https://wa.me/" target="_blank">Back to WhatsApp</a>
</div>
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
