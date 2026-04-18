from app.services.google_calendar import get_today_events_for_user, normalize_events
from app.services.maps.maps_service import estimate_travel_info
from app.models.morning_brief import MorningBriefData
from app.services.weather.weather_service import get_weather_for_today


def compose_morning_insights(user: dict) -> MorningBriefData:
    """
    Returns structured data for the morning brief.
    Calendar + dynamic weather + travel info for first event.
    Requires the full user dict (needs refresh_token, location, language).
    """
    refresh_token = user.get("_refresh_token")
    user_location = user.get("location", "Bogotá, Colombia")
    lang = (user.get("language") or "es").lower()

    # 1. Get events via per-user token
    raw_events = get_today_events_for_user(refresh_token) if refresh_token else []

    # 2. Normalize
    normalized_events = normalize_events(raw_events) if raw_events else []
    event_count = len(normalized_events)

    # 3. Weather
    weather_info = get_weather_for_today(user_city=user_location, lang=lang)

    # 4. First event + travel
    first_event = None
    if event_count > 0:
        first_event = normalized_events[0]

        if first_event.get("location"):
            first_event["has_location"] = True
            location = first_event["location"]

            try:
                leave_at, duration_minutes = estimate_travel_info(
                    destination=location,
                    departure_time_iso=first_event.get("start"),
                    origin=user_location,
                )
                if leave_at is not None and duration_minutes is not None:
                    first_event["leave_at"] = leave_at
                    first_event["traffic_note"] = str(duration_minutes)
                else:
                    first_event["leave_at"] = None
                    first_event["traffic_note"] = None
            except Exception as e:
                first_event["leave_at"] = None
                first_event["traffic_note"] = None
        else:
            first_event["has_location"] = False

    return MorningBriefData(
        event_count=event_count,
        first_event=first_event,
        expense=None,
        balance_warning=None,
        weather=weather_info,
    )
