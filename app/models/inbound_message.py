from typing import Literal, Optional
from pydantic import BaseModel

class InboundMessage(BaseModel):
    """
    Canonical internal representation of a user message after webhook normalization

    This model isolates the rest of the application from provider-specific webhook payloads shapes.
    """

    user_phone_number: str #User's identifier, their phone number in WhatsApp's.
    message_id: str
    channel: Literal["whatsapp"] = "whatsapp"
    message_type: Literal["text", "audio"]
    text: Optional[str] = None
    audio_id: Optional[str] = None
    timestamp: Optional[str] = None


