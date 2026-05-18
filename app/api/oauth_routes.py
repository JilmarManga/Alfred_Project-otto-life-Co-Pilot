import logging

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.repositories.user_repository import UserRepository
from app.services import google_oauth, microsoft_oauth, google_drive_oauth
from app.services import google_calendar, microsoft_calendar
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
  .logo{width:120px;height:120px;object-fit:contain;margin-bottom:4px}
  .brand{font-size:24px;font-weight:700;color:#d4006c;letter-spacing:.5px;margin-bottom:24px}
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
  <img src="/static/logo.png" alt="Otto" class="logo">
  <div class="brand">Otto</div>
  <div class="icon">⚠️</div>
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
  <img src="/static/logo.png" alt="Otto" class="logo">
  <div class="brand">Otto</div>
  <div class="icon">✅</div>
  <h1>You're all set!</h1>
  <p class="sub">Your calendar is connected. Otto will take it from here — your first briefing arrives tomorrow morning.</p>
  <a class="cta" href="https://wa.me/" target="_blank">Back to WhatsApp</a>
</div>
</body></html>
"""


_CALENDAR_SERVICE = {
    "google": google_calendar,
    "microsoft": microsoft_calendar,
}


def _exchange_failed_copy(language: str) -> str:
    return (
        "I couldn't connect your account just now — please try the link again."
        if language == "en"
        else "No pude conectar tu cuenta en este momento — intenta el enlace de nuevo."
    )


def _connected_copy(language: str, event_count: int, slot: str) -> str:
    """Bilingual confirmation. Primary = onboarding wording (first briefing
    tomorrow); secondary = 'second account, calendars now merged'."""
    en = language == "en"
    if slot == "secondary":
        if en:
            return ("Second account connected ✅\n\nI'll now combine events "
                    "from both calendars. Ask me anything.")
        return ("Segunda cuenta conectada ✅\n\nAhora combinaré los eventos de "
                "ambos calendarios. Pregúntame lo que quieras.")
    if event_count == 0:
        return (
            "Calendar connected ✅\n\nNothing on your calendar today — enjoy it 🙂\n\nYour first briefing arrives tomorrow morning. Ask me anything in the meantime."
            if en
            else "Calendario conectado ✅\n\nNo tienes nada hoy — disfrútalo 🙂\n\nTu primer resumen llega mañana por la mañana. Mientras tanto, pregúntame lo que quieras."
        )
    return (
        f"Calendar connected ✅\n\nYou have {event_count} event(s) today.\nYour first briefing arrives tomorrow morning.\n\nUntil then, ask me anything."
        if en
        else f"Calendario conectado ✅\n\nTienes {event_count} evento(s) hoy.\nTu primer resumen llega mañana por la mañana.\n\nMientras tanto, pregúntame lo que quieras."
    )


def _finalize_connection(user: dict, provider: str, refresh_token: str):
    """Shared success path for both providers. Encrypt → save into the right
    connected_accounts slot → confirm. Returns a RedirectResponse on success
    or an HTMLResponse on a (gracefully handled) failure."""
    phone = user["phone"]
    language = (user.get("language") or "en").lower()
    slot = user.get("oauth_pending_slot") or "primary"

    try:
        encrypted = encrypt(refresh_token)
    except TokenCryptoError as exc:
        logger.exception("Token encryption failed for %s: %s", phone, exc)
        return HTMLResponse(_EXPIRED_PAGE, status_code=500)

    UserRepository.save_connected_account(
        phone,
        provider=provider,
        encrypted_refresh_token=encrypted,
        slot=slot,
    )
    UserRepository.clear_oauth_state(phone)
    if slot == "primary":
        # Onboarding / primary (re)connect mirrors the original Google flow.
        UserRepository.set_calendar_reminders_enabled(phone, True)
        UserRepository.set_onboarding_state(phone, "completed")

    try:
        events = _CALENDAR_SERVICE[provider].get_today_events_for_user(refresh_token)
        send_whatsapp_message(phone, _connected_copy(language, len(events), slot))
    except Exception as exc:
        logger.exception("Post-callback event fetch failed for %s: %s", phone, exc)
        send_whatsapp_message(
            phone,
            "Calendar connected ✅ — I'll have your briefing ready tomorrow morning."
            if language == "en"
            else "Calendario conectado ✅ — tendré tu resumen listo mañana por la mañana.",
        )

    return RedirectResponse(url="/auth/done")


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
        # Preserve the provider/slot chosen at link-mint time (a Google
        # second-account add must stay slot='secondary').
        UserRepository.set_oauth_state_token(
            user["phone"], state,
            user.get("google_oauth_state_expires_at"),
            code_verifier=code_verifier,
            provider="google",
            slot=user.get("oauth_pending_slot") or "primary",
        )

    return RedirectResponse(url)


@router.get("/auth/google/callback")
async def callback(request: Request):
    """
    Google redirects here after user consent.
    Exchange code → refresh_token, encrypt, save into the right account slot,
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
        logger.exception("Google OAuth code exchange failed for %s: %s", phone, exc)
        send_whatsapp_message(phone, _exchange_failed_copy((user.get("language") or "en").lower()))
        return HTMLResponse(_EXPIRED_PAGE, status_code=500)

    return _finalize_connection(user, "google", refresh_token)


@router.get("/auth/microsoft/authorize")
async def microsoft_authorize(state: str = ""):
    """Redirect the user to Microsoft's consent screen (mirrors Google)."""
    if not state:
        return HTMLResponse(_EXPIRED_PAGE, status_code=400)

    user = UserRepository.get_user_by_oauth_state(state)
    if not user:
        return HTMLResponse(_EXPIRED_PAGE, status_code=400)

    try:
        url, pkce_blob = microsoft_oauth.build_authorize_url(state_token=state)
    except Exception as exc:
        logger.exception("Failed to build Microsoft authorize URL: %s", exc)
        return HTMLResponse(_EXPIRED_PAGE, status_code=500)

    UserRepository.set_oauth_state_token(
        user["phone"], state,
        user.get("google_oauth_state_expires_at"),
        code_verifier=pkce_blob,
        provider="microsoft",
        slot=user.get("oauth_pending_slot") or "primary",
    )

    return RedirectResponse(url)


@router.get("/auth/microsoft/callback")
async def microsoft_callback(request: Request):
    """Microsoft redirects here after consent. MSAL validates PKCE + state
    from the stored flow blob, then we reuse the shared success path."""
    params = dict(request.query_params)
    code = params.get("code")
    state = params.get("state")
    error = params.get("error")

    if error or not code or not state:
        logger.warning("MS OAuth callback missing code/state or has error: %s", params)
        return HTMLResponse(_EXPIRED_PAGE, status_code=400)

    user = UserRepository.get_user_by_oauth_state(state)
    if not user:
        logger.warning("MS OAuth callback: no user for state token (expired or unknown)")
        return HTMLResponse(_EXPIRED_PAGE, status_code=400)

    phone = user["phone"]

    try:
        pkce_blob = user.get("microsoft_oauth_flow")
        refresh_token = microsoft_oauth.exchange_code(pkce_blob, params)
    except Exception as exc:
        logger.exception("Microsoft OAuth code exchange failed for %s: %s", phone, exc)
        send_whatsapp_message(phone, _exchange_failed_copy((user.get("language") or "en").lower()))
        return HTMLResponse(_EXPIRED_PAGE, status_code=500)

    return _finalize_connection(user, "microsoft", refresh_token)


@router.get("/auth/done")
async def done():
    return HTMLResponse(_DONE_PAGE)


# --------------------------------------------------------------------------- #
# Google Drive OAuth — fully isolated from the calendar flow above.            #
# Uses the google_drive_oauth_* state namespace and writes ONLY the Drive      #
# refresh token. It never calls _finalize_connection / save_connected_account  #
# so a Drive consent can never mutate a calendar account.                      #
# --------------------------------------------------------------------------- #

_DRIVE_DONE_PAGE = f"""
<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Drive connected — Otto</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
{_PAGE_STYLE}
</head><body>
<div class="card accent-green">
  <img src="/static/logo.png" alt="Otto" class="logo">
  <div class="brand">Otto</div>
  <div class="icon">✅</div>
  <h1>Drive connected!</h1>
  <p class="sub">Otto can now read and (only with your explicit confirmation) edit your files. Head back to WhatsApp and ask away.</p>
  <a class="cta" href="https://wa.me/" target="_blank">Back to WhatsApp</a>
</div>
</body></html>
"""


def _drive_connected_copy(language: str) -> str:
    return (
        "Drive connected ✅\n\nYou can ask me to find, read or analyze a file. "
        "I'll always show you the change and wait for your *yes* before editing anything."
        if language == "en"
        else "Drive conectado ✅\n\nPídeme buscar, leer o analizar un archivo. "
        "Siempre te muestro el cambio y espero tu *sí* antes de editar algo."
    )


@router.get("/auth/google-drive/authorize")
async def drive_authorize(state: str = ""):
    """Redirect the user to Google's Drive consent screen."""
    if not state:
        return HTMLResponse(_EXPIRED_PAGE, status_code=400)

    user = UserRepository.get_user_by_drive_oauth_state(state)
    if not user:
        return HTMLResponse(_EXPIRED_PAGE, status_code=400)

    try:
        url, code_verifier = google_drive_oauth.build_authorize_url(state_token=state)
    except Exception as exc:
        logger.exception("Failed to build Drive authorize URL: %s", exc)
        return HTMLResponse(_EXPIRED_PAGE, status_code=500)

    if code_verifier:
        UserRepository.set_drive_oauth_state_token(
            user["phone"], state,
            user.get("google_drive_oauth_state_expires_at"),
            code_verifier=code_verifier,
        )

    return RedirectResponse(url)


@router.get("/auth/google-drive/callback")
async def drive_callback(request: Request):
    """Google redirects here after Drive consent. Exchange code → refresh
    token, encrypt, save into the Drive-only fields, confirm via WhatsApp."""
    params = dict(request.query_params)
    code = params.get("code")
    state = params.get("state")
    error = params.get("error")

    if error or not code or not state:
        logger.warning("Drive OAuth callback missing code/state or has error: %s", params)
        return HTMLResponse(_EXPIRED_PAGE, status_code=400)

    user = UserRepository.get_user_by_drive_oauth_state(state)
    if not user:
        logger.warning("Drive OAuth callback: no user for state token (expired or unknown)")
        return HTMLResponse(_EXPIRED_PAGE, status_code=400)

    phone = user["phone"]
    language = (user.get("language") or "en").lower()

    try:
        code_verifier = user.get("google_drive_oauth_code_verifier")
        refresh_token = google_drive_oauth.exchange_code(
            code=code, state_token=state, code_verifier=code_verifier,
        )
    except Exception as exc:
        logger.exception("Drive OAuth code exchange failed for %s: %s", phone, exc)
        send_whatsapp_message(phone, _exchange_failed_copy(language))
        return HTMLResponse(_EXPIRED_PAGE, status_code=500)

    try:
        encrypted = encrypt(refresh_token)
    except TokenCryptoError as exc:
        logger.exception("Drive token encryption failed for %s: %s", phone, exc)
        return HTMLResponse(_EXPIRED_PAGE, status_code=500)

    UserRepository.save_drive_credentials(phone, encrypted)
    UserRepository.clear_drive_oauth_state(phone)
    send_whatsapp_message(phone, _drive_connected_copy(language))

    return RedirectResponse(url="/auth/drive-done")


@router.get("/auth/drive-done")
async def drive_done():
    return HTMLResponse(_DRIVE_DONE_PAGE)
