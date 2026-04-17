import logging
import os
import secrets
from datetime import datetime, timedelta
from typing import Optional

from app.handlers import onboarding_copy
from app.models.inbound_message import InboundMessage
from app.parser.name_city_extractor import extract_name_and_city
from app.repositories.unknown_message_repository import UnknownMessageRepository
from app.repositories.user_repository import UserRepository
from app.services.location_resolver import (
    resolve_location,
    STATUS_AMBIGUOUS,
    STATUS_API_ERROR,
    STATUS_NOT_FOUND,
    STATUS_RESOLVED,
)
from app.services.whatsapp_sender import send_whatsapp_message

logger = logging.getLogger(__name__)

STATE_LANGUAGE_PENDING = "language_pending"
STATE_BETA_PENDING = "beta_pending"
STATE_PROFILE_PENDING = "profile_pending"
STATE_LOCATION_RETRY = "location_retry"
STATE_OAUTH_PENDING = "oauth_pending"
STATE_COMPLETED = "completed"

_CALENDAR_INTENT_KEYWORDS = {
    "calendar", "calendario", "schedule", "agenda", "reunion", "reunión",
    "meeting", "event", "evento", "today", "hoy", "tengo", "have", "day",
    "mañana", "tomorrow", "busy",
}


def _detect_language(text: str) -> Optional[str]:
    cleaned = (text or "").strip().lower()
    if "🇬🇧" in text or "🇺🇸" in text:
        return "en"
    if "🇨🇴" in text or "🇪🇸" in text or "🇲🇽" in text:
        return "es"
    words = set(cleaned.replace(",", " ").split())
    # Spanish check first — "en español" contains "en" but is clearly Spanish.
    if words & {"español", "espanol", "spanish", "es", "esp", "sp", "2"}:
        return "es"
    if words & {"english", "inglés", "ingles", "en", "eng", "1"}:
        return "en"
    return None


def _derive_state(user: Optional[dict]) -> str:
    if not user:
        return STATE_LANGUAGE_PENDING
    if user.get("onboarding_state"):
        return user["onboarding_state"]
    if user.get("onboarding_completed"):
        return STATE_COMPLETED
    if not user.get("language"):
        return STATE_LANGUAGE_PENDING
    return STATE_PROFILE_PENDING


def _build_authorize_url(state_token: str) -> str:
    base = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
    return f"{base}/auth/google/authorize?state={state_token}"


def _send_oauth_link(phone: str, user: dict) -> None:
    state_token = secrets.token_urlsafe(32)
    expires_at = datetime.utcnow() + timedelta(hours=1)
    UserRepository.set_oauth_state_token(phone, state_token, expires_at)
    link = _build_authorize_url(state_token)
    lang = (user.get("language") or "en").lower()
    name = user.get("name") or ""
    send_whatsapp_message(phone, onboarding_copy.get("oauth_link", lang, name=name, link=link))
    UserRepository.mark_oauth_link_sent(phone)
    UserRepository.set_onboarding_state(phone, STATE_OAUTH_PENDING)


def _handle_location_result(phone: str, user: dict, raw_city: str, result) -> None:
    lang = (user.get("language") or "en").lower()

    if result.status == STATUS_RESOLVED:
        UserRepository.save_resolved_location(
            phone,
            location=result.normalized_name,
            latitude=result.latitude,
            longitude=result.longitude,
            timezone=result.timezone,
        )
        updated = UserRepository.get_user(phone) or user
        _send_oauth_link(phone, updated)
        return

    if result.status == STATUS_NOT_FOUND:
        UserRepository.set_onboarding_state(phone, STATE_LOCATION_RETRY)
        UnknownMessageRepository.log(
            phone, raw_city, "location_retry_failed",
            language=lang, onboarding_state=STATE_LOCATION_RETRY,
        )
        send_whatsapp_message(phone, onboarding_copy.get("city_not_found", lang))
        return

    if result.status == STATUS_AMBIGUOUS:
        UserRepository.set_onboarding_state(phone, STATE_LOCATION_RETRY)
        send_whatsapp_message(
            phone, onboarding_copy.get("city_ambiguous", lang, city=raw_city)
        )
        return

    UserRepository.create_or_update_user(phone, {
        "location_raw": raw_city,
        "location_resolution_status": "pending_retry",
        "timezone": "UTC",
    })
    updated = UserRepository.get_user(phone) or user
    _send_oauth_link(phone, updated)


async def handle_onboarding(inbound: InboundMessage, user: Optional[dict]) -> bool:
    """
    Onboarding V1.0.0 state machine. Returns True if the message was consumed.
    """
    phone = inbound.user_phone_number
    text = (inbound.text or "").strip()

    if user is None:
        UserRepository.create_or_update_user(phone, {
            "onboarding_state": STATE_LANGUAGE_PENDING,
            "onboarding_completed": False,
            "language": None,
        })
        send_whatsapp_message(phone, onboarding_copy.get("language_prompt"))
        return True

    state = _derive_state(user)

    if state == STATE_LANGUAGE_PENDING:
        lang = _detect_language(text)
        if lang is None:
            asked = user.get("language_asked_count", 0)
            if asked >= 1:
                lang = "en"
            else:
                UserRepository.create_or_update_user(phone, {"language_asked_count": asked + 1})
                send_whatsapp_message(phone, onboarding_copy.get("language_retry"))
                return True
        UserRepository.create_or_update_user(phone, {"language": lang})
        UserRepository.set_onboarding_state(phone, STATE_BETA_PENDING)
        send_whatsapp_message(phone, onboarding_copy.get("beta_welcome", lang))
        return True

    if state == STATE_BETA_PENDING:
        lang = (user.get("language") or "en").lower()
        UserRepository.set_onboarding_state(phone, STATE_PROFILE_PENDING)
        send_whatsapp_message(phone, onboarding_copy.get("intro", lang))
        return True

    if state == STATE_PROFILE_PENDING:
        lang = (user.get("language") or "en").lower()
        extraction = await extract_name_and_city(text)
        name, city = extraction.name, extraction.city

        if not name and not city:
            send_whatsapp_message(phone, onboarding_copy.get("ask_profile_retry", lang))
            return True

        if name:
            UserRepository.create_or_update_user(phone, {"name": name})

        if not city:
            send_whatsapp_message(phone, onboarding_copy.get("ask_city_only", lang, name=name or ""))
            return True

        if not name and not user.get("name"):
            UserRepository.create_or_update_user(phone, {"location_raw": city})
            send_whatsapp_message(phone, onboarding_copy.get("ask_name_only", lang))
            return True

        result = resolve_location(city)
        updated = UserRepository.get_user(phone) or user
        _handle_location_result(phone, updated, city, result)
        return True

    if state == STATE_LOCATION_RETRY:
        result = resolve_location(text)
        _handle_location_result(phone, user, text, result)
        return True

    if state == STATE_OAUTH_PENDING:
        lowered = text.lower()
        if any(kw in lowered for kw in _CALENDAR_INTENT_KEYWORDS):
            state_token = user.get("google_oauth_state_token")
            if not state_token:
                state_token = secrets.token_urlsafe(32)
                UserRepository.set_oauth_state_token(
                    phone, state_token, datetime.utcnow() + timedelta(hours=1)
                )
            link = _build_authorize_url(state_token)
            lang = (user.get("language") or "en").lower()
            send_whatsapp_message(
                phone, onboarding_copy.get("oauth_pending_calendar_query", lang, link=link)
            )
            UnknownMessageRepository.log(
                phone, text, "oauth_pending_query",
                language=lang, onboarding_state=STATE_OAUTH_PENDING,
                user_context={"name": user.get("name")},
            )
            return True
        return False

    return False
