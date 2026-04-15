import logging
import os
import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, Header, HTTPException, status

from app.handlers import onboarding_copy
from app.repositories.user_repository import UserRepository
from app.services.location_resolver import resolve_location, STATUS_RESOLVED
from app.services.whatsapp_sender import send_whatsapp_message

logger = logging.getLogger(__name__)

router = APIRouter()


def _require_secret(x_cron_secret: str) -> None:
    expected = os.getenv("CRON_SHARED_SECRET")
    if not expected or x_cron_secret != expected:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or missing cron secret",
        )


def _build_authorize_url(state_token: str) -> str:
    base = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
    return f"{base}/auth/google/authorize?state={state_token}"


@router.post("/cron/oauth-followups")
async def oauth_followups(x_cron_secret: str = Header(default="")):
    """
    Called by the external cron every ~15 minutes. Does two things:
      1. Sends the 3h OAuth reminder to users who haven't connected yet,
         minting a fresh state token so the link is actually clickable.
      2. Retries location resolution for users whose geocoding failed during onboarding.
    """
    _require_secret(x_cron_secret)

    followups_sent = 0
    locations_resolved = 0

    # --- 1. OAuth follow-ups ---
    for user in UserRepository.list_pending_oauth_followups():
        phone = user.get("phone")
        if not phone:
            continue
        try:
            fresh_token = secrets.token_urlsafe(32)
            UserRepository.set_oauth_state_token(
                phone, fresh_token, datetime.utcnow() + timedelta(hours=1)
            )
            link = _build_authorize_url(fresh_token)
            lang = (user.get("language") or "en").lower()
            name = user.get("name") or ""
            msg = onboarding_copy.get("oauth_followup", lang, name=name, link=link)
            send_whatsapp_message(phone, msg)
            UserRepository.mark_oauth_followup_sent(phone)
            followups_sent += 1
        except Exception as exc:
            logger.exception("Failed to send oauth followup to %s: %s", phone, exc)

    # --- 2. Pending location retries ---
    for user in UserRepository.list_pending_location_retries():
        phone = user.get("phone")
        raw_city = user.get("location_raw")
        if not phone or not raw_city:
            continue
        try:
            result = resolve_location(raw_city)
            if result.status == STATUS_RESOLVED:
                UserRepository.save_resolved_location(
                    phone,
                    location=result.normalized_name,
                    latitude=result.latitude,
                    longitude=result.longitude,
                    timezone=result.timezone,
                )
                locations_resolved += 1
        except Exception as exc:
            logger.exception("Location retry failed for %s: %s", phone, exc)

    return {
        "status": "ok",
        "followups_sent": followups_sent,
        "locations_resolved": locations_resolved,
    }
