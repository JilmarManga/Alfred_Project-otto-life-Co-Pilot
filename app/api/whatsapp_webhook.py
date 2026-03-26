import os
from typing import Any

from app.services.message_router import route_incoming_message
from fastapi import APIRouter, HTTPException, Query, Request, status
from app.models.webhook_event import IncomingMessageEvent
from app.services.inbound_message_mapper import map_incoming_event_to_inbound_message
from app.services.intent_classifier import classify_message_intent
from app.services.expense_extractor import extract_expense  # GPT + fallback

from app.repositories.expense_repository import ExpenseRepository
from app.services.response_service import generate_response
from app.services.whatsapp_sender import send_whatsapp_message
from app.ai.ai_service import generate_ai_response
from app.services.google_calendar import get_today_events, normalize_events, summarize_day, describe_next_event, format_events_detailed

from app.db.user_context_store import update_user_context, get_user_context
from app.routers.llm_intent_router import route_with_llm



router = APIRouter()

VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "alfred_verify_token")


# This endpoint is used by Meta to verify the webhook during setup.
@router.get("/webhook")
async def verify_webhook(
    hub_mode: str = Query(alias="hub.mode"),
    hub_verify_token: str = Query(alias="hub.verify_token"),
    hub_challenge: str = Query(alias="hub.challenge"),
) -> int:
    """
    Verify the WhatsApp webhook endpoint with Meta.

    Meta sends these query parameters during webhook setup.
    If the verify token matches, the challenge must be returned.
    """
    if hub_mode != "subscribe":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid hub.mode value.",
        )

    if hub_verify_token != VERIFY_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Webhook verification failed.",
        )

    try:
        return int(hub_challenge)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid hub.challenge value.",
        ) from exc


# This endpoint receives incoming webhook events from WhatsApp.
@router.post("/webhook")
async def receive_webhook(request: Request) -> dict:
    """
    Receive raw WhatsApp webhook events, classify intent,
    and extract structured expense data (AI-native with fallback).
    """
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid JSON payload.",
        ) from exc

    event = await route_incoming_message(payload)
    intent = None
    extracted_expense = None

    if isinstance(event, IncomingMessageEvent):

        # Map to canonical inbound message
        inbound_message = map_incoming_event_to_inbound_message(event)
        print("📩 User message:", inbound_message.text)

        # Get user context
        context = get_user_context(inbound_message.user_phone_number)

        # Try LLM first
        llm_result = route_with_llm(inbound_message.text, context)
        print("🧠 LLM RESULT:", llm_result)

        if llm_result:
            intent = llm_result.get("intent", "unknown")
            index = llm_result.get("index", None)
        else:
            index = None

            # Classify intent
            intent_obj = classify_message_intent(inbound_message)
            intent = intent_obj.intent
            print("🧠 Detected intent:", intent)
            print("📩 User message:", inbound_message.text)

        # If intent is expense, extract structured expense via GPT + fallback
        if intent == "expense":
            extracted_expense = await extract_expense(inbound_message)

            # Persist expense if extraction produced structured data
            if extracted_expense and extracted_expense.amount:
                ExpenseRepository.save_expense(
                    user_phone_number=inbound_message.user_phone_number,
                    expense=extracted_expense,
                )

                # Generate otto response
                fallback_response = generate_response(
                    user_text=inbound_message.text,
                    expense=extracted_expense.dict(),
                    user_stats=None
                )

                # Use AI to generate a more natural, conversational reply, with a fallback to the template-based response
                reply_text = generate_ai_response(
                    f"""
                User message: {inbound_message.text}
                Extracted expense: {extracted_expense.dict()}
                Respond as otto confirming the expense in a natural, short way.
                """,
                    fallback_response=fallback_response
                )

                try:
                    #print(f"otto reply: {reply_text}")
                    print("🐙 otto reply:", reply_text)
                    send_whatsapp_message(
                        inbound_message.user_phone_number,
                        reply_text
                    )
                except Exception as e:
                    print(f"❌ Failed to send WhatsApp message: {e}")

        elif intent == "calendar_followup":
            events = context.get("today_events", [])
            selected_event = None

            if events and index is not None and index < len(events):
                selected_event = events[index]
                reply_text = f"{selected_event['start'].split('T')[1][:5]} — {selected_event['title']}"
            else:
                reply_text = "No encontré ese evento"

            try:
                print("🐙 otto reply:", reply_text)
                send_whatsapp_message(
                    inbound_message.user_phone_number,
                    reply_text
                )
            except Exception as e:
                print(f"❌ Failed to send WhatsApp message: {e}")

        elif intent == "calendar_query":
            events_raw = get_today_events()
            events = normalize_events(events_raw)

            #reply_text = summarize_day(events)
            text = inbound_message.text.lower()

            # Update user context with calendar info for potential follow-ups
            update_user_context(
                inbound_message.user_phone_number,
                "last_intent",
                "calendar_query"
            )

            update_user_context(
                inbound_message.user_phone_number,
                "today_events",
                events
            )

            if llm_result and llm_result.get("list_all"):
                reply_text = format_events_detailed(events)
            else:
                reply_text = summarize_day(events)

            try:
                print("🐙 otto reply:", reply_text)
                send_whatsapp_message(
                    inbound_message.user_phone_number,
                    reply_text
                )
            except Exception as e:
                print(f"❌ Failed to send WhatsApp message: {e}")

    # Return structured response
    return {
        "status": "received",
        "event_type": event.event_type if event else None,
        "intent": intent,
        "extracted_expense": extracted_expense.dict() if extracted_expense else None,
    }