import os
import requests
from datetime import datetime, timedelta

GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY")


def estimate_travel_info(destination: str, departure_time_iso: str):
    """
    Returns:
    - leave_at (str, e.g. "3:00 PM")
    - duration_minutes (int)
    """

    if not GOOGLE_MAPS_API_KEY:
        return None, None

    try:
        now = datetime.now()
        departure_timestamp = int(now.timestamp())

        url = "https://maps.googleapis.com/maps/api/directions/json"

        params = {
            "origin": "Funza, Cundinamarca, Colombia",  # later: replace with user location
            "destination": destination,
            "departure_time": departure_timestamp,
            "key": GOOGLE_MAPS_API_KEY,
        }

        response = requests.get(url, params=params)
        data = response.json()

        routes = data.get("routes", [])
        if not routes:
            return None, None

        leg = routes[0]["legs"][0]

        duration_sec = leg.get("duration_in_traffic", {}).get("value")

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
        return None, None