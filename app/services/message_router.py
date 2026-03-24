from typing import Any

from app.models.webhook_event import (
    IncomingMessageEvent,
    MessageStatusEvent,
    UnsupportedEvent,
    WebhookEvent,
)


async def route_incoming_message(payload: dict[str, Any]) -> WebhookEvent:
    """
    Normalize raw WhatsApp webhook payloads into typed internal events.
    """
    try:
        entry = payload["entry"][0]
        change = entry["changes"][0]
        value = change["value"]

        messages = value.get("messages", [])
        if messages:
            message = messages[0]
            message_type = message.get("type")
            sender_phone = message.get("from")
            message_id = message.get("id")
            timestamp = message.get("timestamp")

            if not sender_phone or not message_type or not message_id:
                return UnsupportedEvent(raw_payload=payload)

            if message_type == "text":
                text_body = message.get("text", {}).get("body", "").strip()
                if not text_body:
                    return UnsupportedEvent(raw_payload=payload)

                return IncomingMessageEvent(
                    from_phone=sender_phone,
                    timestamp=timestamp,
                    message_id=message_id,
                    message_type="text",
                    text=text_body,
                    audio_id=None,
                    raw_payload=payload,
                )

            if message_type == "audio":
                audio_id = message.get("audio", {}).get("id")
                if not audio_id:
                    return UnsupportedEvent(raw_payload=payload)

                return IncomingMessageEvent(
                    from_phone=sender_phone,
                    timestamp=timestamp,
                    message_id=message_id,
                    message_type="audio",
                    text=None,
                    audio_id=audio_id,
                    raw_payload=payload,
                )

            return UnsupportedEvent(raw_payload=payload)

        statuses = value.get("statuses", [])
        if statuses:
            status_event = statuses[0]
            message_id = status_event.get("id")
            status = status_event.get("status")
            recipient_phone = status_event.get("recipient_id")
            timestamp = status_event.get("timestamp")

            if (
                not message_id
                or not status
                or not recipient_phone
                or status not in {"sent", "delivered", "read"}
            ):
                return UnsupportedEvent(raw_payload=payload)

            return MessageStatusEvent(
                message_id=message_id,
                status=status,
                recipient_phone=recipient_phone,
                timestamp=timestamp,
                raw_payload=payload,
            )

        return UnsupportedEvent(raw_payload=payload)

    except (KeyError, IndexError, TypeError):
        return UnsupportedEvent(raw_payload=payload)