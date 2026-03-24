from app.models.inbound_message import InboundMessage
from app.models.webhook_event import IncomingMessageEvent

def map_incoming_event_to_inbound_message(event: IncomingMessageEvent) -> InboundMessage:
    """
    Convert a normalized incoming whatsapp event (IncomingMessageEvent) into Alfre's canonical internal inbound message model (InboundMessage).
    """
    return InboundMessage(
        user_phone_number=event.from_phone,
        message_id=event.message_id,
        channel="whatsapp",
        message_type=event.message_type,
        text=event.text,
        audio_id=event.audio_id,
        timestamp=event.timestamp,
    )