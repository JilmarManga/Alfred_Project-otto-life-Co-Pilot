from app.services.google_calendar import get_today_events, normalize_events
from app.services.maps.maps_service import estimate_travel_info
from app.models.morning_brief import MorningBriefData


def compose_morning_insights(user_id: str) -> MorningBriefData:
    """
    Returns structured data for the morning brief.
    Calendar only (for now).
    """

    # 1. Get events
    #events = get_today_events(user_id)
    events = get_today_events()

    # 2. Normalize
    normalized_events = normalize_events(events) if events else []

    # 3. Count
    event_count = len(normalized_events)

    # 4. First event
    first_event = None

    if event_count > 0:
        first_event = normalized_events[0]

        if first_event.get("location"):
            first_event["has_location"] = True

            location = first_event.get("location")
            leave_at, duration_minutes = estimate_travel_info(
                location,
                first_event.get("start")
            )

            # fallback if API not available
            if not leave_at:
                leave_at = None
                traffic_note = None
            else:
                traffic_note = f"{duration_minutes} min"

            first_event["leave_at"] = leave_at
            first_event["traffic_note"] = traffic_note
        else:
            first_event["has_location"] = False

    return MorningBriefData(
    event_count=event_count,
    first_event=first_event,
    expense=None,
    balance_warning=None,
    weather={
        "summary": "Clima no disponible"
    }
)