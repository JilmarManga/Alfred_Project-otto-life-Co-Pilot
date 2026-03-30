from app.db.user_context_store import get_user_context, update_user_context
from app.services.google_calendar import get_today_events, normalize_events
from app.services.maps.maps_service import estimate_travel_info
from app.models.morning_brief import MorningBriefData
from app.services.weather.weather_service import get_weather_for_today



def compose_morning_insights(user_id: str) -> MorningBriefData:
    """
    Returns structured data for the morning brief.
    Calendar + dynamic weather + travel info for first event.
    """

    # 1. Get events
    events = get_today_events()

    # 2. Normalize
    normalized_events = normalize_events(events) if events else []

    # 3. Count
    event_count = len(normalized_events)

    # 4. Weather info
    # Get user context for dynamic location
    context = get_user_context(user_id)
    user_city = context.get("last_known_location", "Bogotá, Colombia")  # fallback if unknown

    # Fetch weather dynamically based on user location
    weather_info = get_weather_for_today(user_city=user_city)

    # 5. First event
    first_event = None

    if event_count > 0:
        first_event = normalized_events[0]

        if first_event.get("location"):
            first_event["has_location"] = True
            location = first_event.get("location")

            # Use user's last known location as origin
            user_context = get_user_context(user_id)
            user_origin = user_context.get("last_known_location")

            try:
                leave_at, duration_minutes = estimate_travel_info(
                    location,
                    first_event.get("start"),
                )
                print(f"🔍 DEBUG: leave_at={leave_at}, duration_minutes={duration_minutes}")
                print(f"🔍 DEBUG: first_event location: {location}")
                print(f"🔍 DEBUG: first_event start time: {first_event.get('start')}")

                # Use traffic info if API returns valid data
                if leave_at is not None and duration_minutes is not None:
                    first_event["leave_at"] = leave_at
                    first_event["traffic_note"] = str(duration_minutes)
                else:
                    first_event["leave_at"] = None
                    first_event["traffic_note"] = None

            except Exception as e:
                print(f"❌ Travel estimation error: {e}")
                first_event["leave_at"] = None
                first_event["traffic_note"] = None
        else:
            first_event["has_location"] = False

    # Save normalized events in user context for follow-up queries
    if normalized_events:
        try:
            update_user_context(user_id, 'today_events', normalized_events)
        except Exception as e:
            print(f"❌ Could not save events to user context: {e}")

    return MorningBriefData(
        event_count=event_count,
        first_event=first_event,
        expense=None,
        balance_warning=None,
        weather=weather_info
    )