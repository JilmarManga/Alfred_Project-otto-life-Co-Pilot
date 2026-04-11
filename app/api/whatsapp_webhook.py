import os
import logging

from fastapi import APIRouter, HTTPException, Query, Request, status
from app.models.webhook_event import IncomingMessageEvent
from app.services.message_router import route_incoming_message
from app.services.inbound_message_mapper import map_incoming_event_to_inbound_message
from app.services.whatsapp_sender import send_whatsapp_message
from app.repositories.user_repository import UserRepository
from app.handlers.onboarding_handler import handle_onboarding
from app.parser.message_parser import parse_message
from app.router.deterministic_router import route
from app.responder.response_formatter import format_response

logger = logging.getLogger(__name__)

router = APIRouter()
VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "alfred_verify_token")


@router.get("/webhook")
async def verify_webhook(
    hub_mode: str = Query(alias="hub.mode"),
    hub_verify_token: str = Query(alias="hub.verify_token"),
    hub_challenge: str = Query(alias="hub.challenge"),
) -> int:
    if hub_mode != "subscribe":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid hub.mode value.")
    if hub_verify_token != VERIFY_TOKEN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Webhook verification failed.")
    try:
        return int(hub_challenge)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid hub.challenge value.") from exc


@router.post("/webhook")
async def receive_webhook(request: Request) -> dict:
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON payload.") from exc

    event = await route_incoming_message(payload)

    if not isinstance(event, IncomingMessageEvent):
        return {"status": "ignored"}

    inbound = map_incoming_event_to_inbound_message(event)
    phone = inbound.user_phone_number
    logger.info("Incoming message from %s: %s", phone, inbound.text)

    user = UserRepository.get_user(phone)

    if handle_onboarding(inbound, user):
        return {"status": "onboarding"}

    # Enrich user dict with phone (Firestore doc.to_dict() doesn't include the doc ID)
    user["phone_number"] = phone

    try:
        parsed = await parse_message(inbound.text)   # Layer 1
        agent  = route(parsed)                        # Layer 2
        result = agent.execute(parsed, user)          # Layer 3
        reply  = format_response(result, user)        # Layer 4
        send_whatsapp_message(phone, reply)
    except Exception as e:
        logger.error("Pipeline error for %s: %s", phone, e)
        lang = (user or {}).get("language", "es")
        fallback = "Ups, algo salió mal. Intenta de nuevo 🙏" if lang == "es" else "Oops, something went wrong. Try again 🙏"
        send_whatsapp_message(phone, fallback)

    return {"status": "processed"}
