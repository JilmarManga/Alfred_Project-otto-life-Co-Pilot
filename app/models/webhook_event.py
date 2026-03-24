
from typing import Optional, Literal
from pydantic import BaseModel, Field


class WebhookEvent(BaseModel):
    """
    Base class for all normalized webhook events.
    """
    event_type: Literal["incoming_message", "message_status", "unsupported"]
    from_phone: Optional[str] = None
    timestamp: Optional[str] = None
    raw_payload: dict = Field(default_factory=dict)


class IncomingMessageEvent(WebhookEvent):
    """
    Normalized structure for an incoming WhatsApp message.
    """
    event_type: Literal["incoming_message"] = "incoming_message"
    message_type: Literal["text", "audio"]
    message_id: str
    text: Optional[str] = None
    audio_id: Optional[str] = None


class MessageStatusEvent(WebhookEvent):
    """
    Normalized structure for a WhatsApp message status update.
    """
    event_type: Literal["message_status"] = "message_status"
    message_id: str
    status: Literal["sent", "delivered", "read"]
    recipient_phone: str


class UnsupportedEvent(WebhookEvent):
    """
    Placeholder for any other unsupported or unknown events.
    """
    event_type: Literal["unsupported"] = "unsupported"