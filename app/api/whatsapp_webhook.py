import os
import logging
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, HTTPException, Query, Request, status
from app.models.webhook_event import IncomingMessageEvent
from app.services.message_router import route_incoming_message
from app.services.inbound_message_mapper import map_incoming_event_to_inbound_message
from app.services.whatsapp_sender import send_whatsapp_message
from app.repositories.user_repository import UserRepository
from app.handlers.onboarding_handler import handle_onboarding
from app.handlers.pending_expense_handler import handle_pending_expense
from app.handlers.pending_event_handler import handle_pending_event
from app.handlers.pending_list_handler import handle_pending_list
from app.handlers.pending_travel_handler import handle_pending_travel
from app.db.user_context_store import update_user_context
from app.models.agent_result import AgentResult
from app.parser.message_parser import parse_message
from app.router.deterministic_router import route
from app.responder.response_formatter import format_response


def _build_parser_context(user: dict) -> dict:
    """Today's date in the user's local timezone + tz name — used by the LLM
    to resolve relative dates like 'next Wednesday' to absolute ISO."""
    tz_name = user.get("timezone") or "UTC"
    try:
        tz = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        tz = ZoneInfo("UTC")
        tz_name = "UTC"
    return {
        "today": datetime.now(tz).date().isoformat(),
        "tz": tz_name,
    }

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

    if await handle_onboarding(inbound, user):
        return {"status": "onboarding"}

    if handle_pending_expense(inbound, user):
        return {"status": "pending_expense"}

    if handle_pending_event(inbound, user):
        return {"status": "pending_event"}

    if handle_pending_travel(inbound, user):
        return {"status": "pending_travel"}

    if handle_pending_list(inbound, user):
        return {"status": "pending_list"}

    # Enrich user dict with phone (Firestore doc.to_dict() doesn't include the doc ID)
    user["phone_number"] = phone

    try:
        parser_context = _build_parser_context(user)
        parsed = await parse_message(inbound.text, user_context=parser_context)  # Layer 1
        decision = route(parsed)                                                  # Layer 2

        # Router returned two candidates — ask the user which action they meant
        # and stash the original ParsedMessage so gate 5 can re-dispatch.
        if decision.disambiguation:
            update_user_context(phone, "pending_list", {
                "step": "awaiting_disambiguation",
                "candidates": list(decision.disambiguation.candidates),
                "original_parsed": parsed,
            })
            synthetic = AgentResult(
                agent_name="ListAgent",
                success=True,
                data={
                    "type": "list_disambiguation",
                    "candidates": list(decision.disambiguation.candidates),
                },
            )
            reply = format_response(synthetic, user)
            send_whatsapp_message(phone, reply)
            return {"status": "list_disambiguation"}

        agent  = decision.agent                                                   # Layer 2
        result = agent.execute(parsed, user)                                      # Layer 3
        reply  = format_response(result, user)                                    # Layer 4
        send_whatsapp_message(phone, reply)

        # Optional second message for agents that return a follow_up_message
        # (e.g. CalendarAgent creation → "¿Quieres más detalles?").
        follow_up = (result.data or {}).get("follow_up_message")
        if follow_up:
            send_whatsapp_message(phone, follow_up)
    except Exception as e:
        logger.error("Pipeline error for %s: %s", phone, e)
        lang = (user or {}).get("language", "es")
        fallback = "Ups, algo salió mal. Intenta de nuevo 🙏" if lang == "es" else "Oops, something went wrong. Try again 🙏"
        send_whatsapp_message(phone, fallback)

    return {"status": "processed"}
