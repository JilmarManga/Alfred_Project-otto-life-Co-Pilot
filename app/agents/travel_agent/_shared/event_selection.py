from datetime import datetime, timezone
from typing import Optional


def find_next_upcoming_event(events: list, now: Optional[datetime] = None) -> Optional[dict]:
    """Return the first event whose start is strictly after *now* (tz-aware comparison).
    Falls back to the last event in the list when all events are in the past.
    Returns None for an empty list.

    Mirrors CalendarAgent._find_next_upcoming_event but lives here so TravelSkills
    don't import from another agent's module.
    """
    if not events:
        return None

    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    upcoming = []
    for event in events:
        start_raw = event.get("start")
        if not start_raw:
            continue
        try:
            dt = datetime.fromisoformat(start_raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if dt > now:
                upcoming.append((dt, event))
        except (ValueError, TypeError):
            continue

    if upcoming:
        return min(upcoming, key=lambda x: x[0])[1]

    return events[-1]
