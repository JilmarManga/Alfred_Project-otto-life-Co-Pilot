from typing import Optional, List, Literal
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
    event_title: Optional[str] = Field(None, description="Title of the event to create (calendar creation intent).")
    event_start: Optional[str] = Field(None, description="ISO 8601 start datetime with tz offset for the event to create.")
    event_location: Optional[str] = Field(None, description="Location for the event to create, if any.")
    event_duration_minutes: Optional[int] = Field(None, description="Event duration in minutes; default 60 applied at creation time.")
    list_intent: Optional[Literal["save", "recall", "delete"]] = Field(None, description="User's list operation intent (save/recall/delete), or None.")
    list_name: Optional[str] = Field(None, description="List name exactly as the user typed it — not normalized or translated.")
    list_item: Optional[str] = Field(None, description="Content to save (URL or plain text), exactly as the user sent it.")
    list_label: Optional[str] = Field(None, description="Optional short label the user attached to this specific item.")
