from app.models.morning_brief import MorningBriefData
from datetime import datetime

def build_morning_message(data: MorningBriefData, language: str, user_name: str | None = None) -> str:
    """
    Builds a deterministic morning brief message.
    No AI. No assumptions. Pure formatting.
    """

    event_count = data.event_count
    first_event = data.first_event
    weather = data.weather or {}

    lines = []

    # 1. Greeting
    if language == "es":
        greeting = "Buenos días"
        if user_name:
            greeting += f" {user_name}"
        greeting += "."
    else:
        greeting = "Good morning"
        if user_name:
            greeting += f" {user_name}"
        greeting += "."

    lines.append(greeting)

    # 2. Event count
    if language == "es":
        lines.append(f"Tienes {event_count} eventos hoy.")
    else:
        lines.append(f"You have {event_count} events today.")

    # 3. First event detail
    if first_event:
        title = first_event.get("title", "Evento")
        start_raw = first_event.get("start", "")

        location = first_event.get("location")
        has_location = first_event.get("has_location", False)
        leave_at = first_event.get("leave_at")
        traffic_note = first_event.get("traffic_note")

        time_str = ""

        if start_raw:
            try:
                dt = datetime.fromisoformat(start_raw)
                if language == "es":
                    time_str = dt.strftime("%I:%M %p").lstrip("0").lower()
                else:
                    time_str = dt.strftime("%I:%M %p").lstrip("0")
            except Exception:
                time_str = start_raw  # fallback

        if language == "es":
            if has_location and location and leave_at and traffic_note:
                lines.append(
                    f"{title} a las {time_str} en {location} (sal a las {leave_at}, {traffic_note})."
                )
            else:
                lines.append(f"El primero es a las {time_str}: {title}.")
        else:
            if has_location and location and leave_at and traffic_note:
                lines.append(
                    f"{title} at {time_str} in {location} (leave at {leave_at}, {traffic_note})."
                )
            else:
                lines.append(f"The first is at {time_str}: {title}.")

    # 4. Weather
    summary = weather.get("summary", "")

    if summary:
        lines.append(summary)

    return " ".join(lines)
