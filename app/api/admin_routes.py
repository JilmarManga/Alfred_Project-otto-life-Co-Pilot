import logging
import os
from typing import List, Literal, Optional, Union

from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel, Field, field_validator

from app.repositories.user_repository import UserRepository
from app.services.whatsapp_sender import send_whatsapp_message_with_status

logger = logging.getLogger(__name__)

router = APIRouter()


def _require_secret(x_cron_secret: str) -> None:
    expected = os.getenv("CRON_SHARED_SECRET")
    if not expected or x_cron_secret != expected:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or missing cron secret",
        )


class BroadcastRequest(BaseModel):
    recipients: Union[List[str], Literal["all"]]
    body_es: str = Field(min_length=1)
    body_en: str = Field(min_length=1)
    confirm_all: bool = False

    @field_validator("recipients")
    @classmethod
    def _validate_recipients(cls, v):
        if v == "all":
            return v
        if not v:
            raise ValueError("recipients list cannot be empty")
        for p in v:
            if not isinstance(p, str) or not p.startswith("+"):
                raise ValueError(f"invalid phone: {p!r} (must start with '+')")
        return v


def _pick_body(user: dict, body_es: str, body_en: str) -> str:
    return body_es if (user.get("language") or "en").lower() == "es" else body_en


def _is_onboarded(user: dict) -> bool:
    return (
        user.get("onboarding_state") == "completed"
        or (user.get("onboarding_completed") is True and not user.get("onboarding_state"))
    )


@router.post("/admin/broadcasts")
def send_broadcast(req: BroadcastRequest, x_cron_secret: str = Header(default="")):
    """
    Operator-only. Sends a bilingual message to selected users (explicit phone
    list or every onboarded user). Sync handler so FastAPI dispatches it to a
    threadpool — the event loop stays free for webhook + OAuth traffic during
    fan-out.
    """
    _require_secret(x_cron_secret)

    if req.recipients == "all" and not req.confirm_all:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="confirm_all must be true when recipients == 'all'",
        )

    explicit_phones: Optional[List[str]]
    if req.recipients == "all":
        users = UserRepository.list_onboarded_users()
        explicit_phones = None
    else:
        explicit_phones = list(dict.fromkeys(req.recipients))
        users = []
        for phone in explicit_phones:
            user = UserRepository.get_user(phone)
            if user:
                user["phone"] = phone
                users.append(user)

    sent = 0
    failed = 0
    skipped_not_onboarded = 0
    skipped_unknown = 0
    errors: List[dict] = []

    if explicit_phones is not None:
        found_phones = {u.get("phone") for u in users}
        for phone in explicit_phones:
            if phone not in found_phones:
                skipped_unknown += 1
                errors.append({"phone": phone, "reason": "user_not_found"})

    for user in users:
        phone = user.get("phone")
        if not phone:
            continue
        if not _is_onboarded(user):
            skipped_not_onboarded += 1
            continue
        try:
            ok, _resp = send_whatsapp_message_with_status(
                phone, _pick_body(user, req.body_es, req.body_en)
            )
            if ok:
                sent += 1
            else:
                failed += 1
                errors.append({"phone": phone, "reason": "send_failed"})
        except Exception as exc:
            failed += 1
            errors.append({"phone": phone, "reason": "send_exception"})
            logger.exception("Broadcast send failed for %s: %s", phone, exc)

    logger.info(
        "Broadcast complete — sent=%d failed=%d skipped_not_onboarded=%d skipped_unknown=%d",
        sent, failed, skipped_not_onboarded, skipped_unknown,
    )
    return {
        "sent": sent,
        "failed": failed,
        "skipped_not_onboarded": skipped_not_onboarded,
        "skipped_unknown": skipped_unknown,
        "errors": errors,
    }
