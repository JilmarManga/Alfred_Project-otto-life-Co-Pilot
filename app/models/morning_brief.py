from pydantic import BaseModel
from typing import Optional, Dict


class MorningBriefData(BaseModel):
    event_count: int
    first_event: Optional[Dict]
    expense: Optional[Dict]
    balance_warning: Optional[Dict]
    weather: Dict

# first_event structure:
# {
#   "title": str,
#   "start": str,
#   "location": str | None,
#   "has_location": bool,
#   "leave_at": str | None,
#   "traffic_note": str | None
# }