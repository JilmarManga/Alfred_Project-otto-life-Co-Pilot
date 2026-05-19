from typing import Optional, List, Literal, Dict, Any
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
    drive_intent: Optional[Literal["find", "read", "analyze", "modify"]] = Field(None, description="User's Google Drive operation intent, or None.")
    drive_file_ref: Optional[str] = Field(None, description="Drive file name exactly as the user referred to it — not normalized or translated.")
    drive_edit: Optional[Dict[str, Any]] = Field(None, description="Structured, deterministic edit spec for a modify intent. Validated by the agent before anything is staged; never applied without explicit user confirmation.")
    drive_query: Optional[Dict[str, Any]] = Field(None, description="Structured tabular query spec for an analyze intent over a spreadsheet (filters/group_by/select/sort/aggregate). Resolved deterministically by query_resolver — the LLM never selects rows. Null when the analyze request is not a structured row query (free-form/prose analysis).")
    reminder_intent: Optional[Literal["set", "list", "cancel"]] = Field(None, description="User's personal-reminder intent (set/list/cancel), or None. Distinct from the calendar reminders on/off setting.")
    reminder_text: Optional[str] = Field(None, description="What to be reminded of, exactly as the user phrased it, WITHOUT the time words. null if not a reminder.")
    reminder_time: Optional[str] = Field(None, description="ISO 8601 datetime with tz offset when the user gave a concrete clock time; a date at 00:00 with tz offset when only a date was given; null otherwise.")
    reminder_period: Optional[Literal["morning", "afternoon", "night"]] = Field(None, description="Part-of-day when the user gave no concrete clock time, or None.")
    reminder_cancel_ref: Optional[str] = Field(None, description="Free-text reference identifying which reminder to cancel, as the user said it. null unless reminder_intent is 'cancel'.")
