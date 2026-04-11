from typing import Optional, List
from pydantic import BaseModel, Field


class EventReference(BaseModel):
    index: Optional[int] = Field(None, description="Position in the event list (0-based).")
    time_reference: Optional[str] = Field(None, description="Time-based reference like '8pm' or 'las 3'.")


class ParsedMessage(BaseModel):
    amount: Optional[float] = Field(None, description="Extracted numeric amount.")
    currency: Optional[str] = Field(None, description="Currency code: COP, USD, EUR, or None.")
    category_hint: Optional[str] = Field(None, description="Category hint from context (e.g. 'arriendo' -> 'housing').")
    date_hint: Optional[str] = Field(None, description="Date reference if mentioned (e.g. 'ayer', 'last week').")
    raw_message: str = Field(..., description="The original user message.")
    signals: List[str] = Field(default_factory=list, description="Intent keywords found in the message.")
    event_reference: Optional[EventReference] = Field(None, description="Calendar event reference for follow-ups.")
