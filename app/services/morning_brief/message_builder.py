from app.models.morning_brief import MorningBriefData
from datetime import datetime

_WEATHER_EMOJI_RULES = [
    ({"tormenta", "thunderstorm", "storm"}, "⛈️"),
    ({"nieve", "snow", "granizo", "hail", "sleet"}, "❄️"),
    ({"lluvia intensa", "heavy rain", "heavy intensity"}, "🌧️"),
    ({"lluvia", "rain", "llovizna", "drizzle", "shower"}, "🌦️"),
    ({"niebla", "neblina", "fog", "mist", "haze", "bruma"}, "🌫️"),
    ({"muy nublado", "nublado", "overcast", "broken clouds"}, "☁️"),
    ({"nubes dispersas", "scattered clouds", "pocas nubes", "few clouds", "parcialmente nublado", "partly"}, "🌥️"),
    ({"despejado", "clear", "soleado", "sunny"}, "☀️"),
]

def _weather_emoji(summary: str) -> str:
    lowered = summary.lower()
    for keywords, emoji in _WEATHER_EMOJI_RULES:
        if any(kw in lowered for kw in keywords):
            return emoji
    return "🌡️"

def format_time_human(iso_str: str, language: str = "es") -> str:
    try:
        dt = datetime.fromisoformat(iso_str)
        formatted = dt.strftime("%I:%M %p").lstrip("0")

        if language == "es":
            return formatted.lower()  # "3:30 pm"
        return formatted  # "3:30 PM"
    except Exception:
        return iso_str

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
        greeting = "☀️ Buenos días"
        if user_name:
            greeting += f" {user_name}"
        greeting += "."
    else:
        greeting = "☀️ Good morning"
        if user_name:
            greeting += f" {user_name}"
        greeting += "."

    lines.append(greeting)

    # 2. Event count
    if language == "es":
        lines.append(f"Tienes {event_count} eventos hoy. 📅")
    else:
        lines.append(f"You have {event_count} events today. 📅")

    # 3. First event detail
    if first_event:
        title = first_event.get("title", "Evento")
        start_raw = first_event.get("start", "")
        location = first_event.get("location", "")
        traffic_note = first_event.get("traffic_note")
        leave_at = first_event.get("leave_at")
        has_location = first_event.get("has_location", False)

        # Format event start time
        time_str = format_time_human(start_raw, language) if start_raw else ""

        '''time_str = ""
        if start_raw:
            try:
                dt = datetime.fromisoformat(start_raw)
                if language == "es":
                    time_str = dt.strftime("%-I:%M %p").lower()
                else:
                    time_str = dt.strftime("%-I:%M %p")
            except Exception:
                time_str = start_raw'''

        # Ensure traffic_str is always computed if traffic_note exists
        traffic_str = None
        if traffic_note:
            try:
                duration_minutes = int(traffic_note)
                hours = duration_minutes // 60
                minutes = duration_minutes % 60

                if hours > 0:
                    if minutes > 0:
                        traffic_str = f"{hours} hora{'s' if hours > 1 else ''} y {minutes} min"
                    else:
                        traffic_str = f"{hours} hora{'s' if hours > 1 else ''}"
                else:
                    traffic_str = f"{minutes} min"
            except Exception:
                traffic_str = traffic_note

        # Build message, always add traffic info if available
        if language == "es":
            if has_location and location:
                lines.append(f"El primero es: {title}, a las {time_str} en {location}.")
                if traffic_str or leave_at:
                    traffic_part = f"{traffic_str}" if traffic_str else "tráfico info no disponible"
                    leave_part = f", sal {leave_at}" if leave_at else ""
                    lines.append(f"Tráfico: {traffic_part}{leave_part}.")
            else:
                lines.append(f"El primero es a las {time_str}: {title}.")
        else:
            if has_location and location:
                lines.append(f"The first is: {title} at {time_str}, in {location}.")
                if traffic_str or leave_at:
                    traffic_part = f"{traffic_str}" if traffic_str else "traffic info unavailable"
                    leave_part = f", leave at {leave_at}" if leave_at else ""
                    lines.append(f"Traffic: {traffic_part}{leave_part}.")
            else:
                lines.append(f"The first is at {time_str}: {title}.")

    # 4. Weather
    summary = weather.get("summary", "")
    temperature = weather.get("temperature")
    if summary:
        label = "Clima" if language == "es" else "Weather"
        emoji = _weather_emoji(summary)
        weather_msg = f"{label}: {summary}, {emoji} {temperature}" if temperature else f"{label}: {summary} {emoji}"
        lines.append(weather_msg)

    # Debugging output
    print("🔍 DEBUG — Morning Briefing Generated:")
    print("🧠 LLM result data:", data)
    print("🐙 Otto reply message:", " ".join(lines))

    return " ".join(lines)
