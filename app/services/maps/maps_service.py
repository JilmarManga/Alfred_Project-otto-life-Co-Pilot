import os
import logging
import requests
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)
GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY")


'''def estimate_travel_info(destination: str, departure_time_iso: str):
    """
    Returns:
    - leave_at (str, e.g. "3:00 PM")
    - duration_minutes (int)
    """

    if not GOOGLE_MAPS_API_KEY:
        return None, None

    try:
        event_time = datetime.fromisoformat(departure_time_iso)
        departure_timestamp = int(event_time.timestamp())

        url = "https://maps.googleapis.com/maps/api/directions/json"

        origin = "Bogotá, Colombia"  # Default origin if user location is unknown
        params = {
            "origin": origin,
            "departure_time": departure_timestamp,
            "key": GOOGLE_MAPS_API_KEY,
        }

        response = requests.get(url, params=params, timeout=10)
        data = response.json()

        routes = data.get("routes", [])
        if not routes:
            return None, None

        leg = routes[0]["legs"][0]

        duration_sec = leg.get("duration_in_traffic", {}).get("value")

        if not duration_sec:
            duration_sec = leg.get("duration", {}).get("value")

        if not duration_sec:
            return None, None

        duration_minutes = int(duration_sec / 60)

        # Calculate leave time
        event_time = datetime.fromisoformat(departure_time_iso)
        leave_time = event_time - timedelta(minutes=duration_minutes)

        leave_at_str = leave_time.strftime("%I:%M %p").lstrip("0")

        return leave_at_str, duration_minutes

    except Exception as e:
        print("Maps error:", e)
        return None, None'''

# Temporary helper to convert minutes to human-readable format for traffic note
def format_duration_human(minutes: int) -> str:
    if minutes is None:
        return ""

    hours = minutes // 60
    mins = minutes % 60

    if hours > 0 and mins > 0:
        return f"{hours} hora{'s' if hours > 1 else ''} y {mins} min"
    elif hours > 0:
        return f"{hours} hora{'s' if hours > 1 else ''}"
    else:
        return f"{mins} min"


# Refactored to match new API signature and handle missing data gracefully
def estimate_travel_info(destination: str, departure_time_iso: str, origin: str = "Bogotá, Colombia"):
    """
    Returns:
    - leave_at (str, e.g. "3:00 PM")
    - duration_minutes (int)
    """

    if not GOOGLE_MAPS_API_KEY:
        logger.warning("GOOGLE_MAPS_API_KEY not set — skipping travel estimate")
        return None, None

    try:
        now = datetime.now()
        departure_timestamp = int(now.timestamp())

        url = "https://maps.googleapis.com/maps/api/directions/json"

        params = {
            "origin": origin,
            "destination": destination,
            "departure_time": departure_timestamp,
            "key": GOOGLE_MAPS_API_KEY,
        }

        response = requests.get(url, params=params, timeout=10)
        data = response.json()

        routes = data.get("routes", [])
        if not routes:
            logger.warning("Maps API returned no routes. Status: %s | origin=%s destination=%s", data.get("status"), origin, destination)
            return None, None

        leg = routes[0]["legs"][0]

        # Try traffic-aware duration first, fall back to regular duration
        duration_sec = leg.get("duration_in_traffic", {}).get("value")
        if not duration_sec:
            duration_sec = leg.get("duration", {}).get("value")

        if not duration_sec:
            logger.warning("Maps API returned route but no duration for %s → %s", origin, destination)
            return None, None

        duration_minutes = int(duration_sec / 60)

        # Calculate leave time
        event_time = datetime.fromisoformat(departure_time_iso)
        leave_time = event_time - timedelta(minutes=duration_minutes)

        leave_at_str = leave_time.strftime("%I:%M %p").lstrip("0")

        return leave_at_str, duration_minutes

    except Exception as e:
        logger.error("Maps error for %s → %s: %s", origin, destination, e)
        return None, None