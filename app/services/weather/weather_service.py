# app/services/weather_service.py

import os
import requests
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("OPENWEATHER_API_KEY")
BASE_URL = "https://api.openweathermap.org/data/2.5/weather"
FORECAST_URL = "https://api.openweathermap.org/data/2.5/forecast"

def get_weather_for_today(user_city: str, lang: str = "es") -> dict:
    """
    Returns current weather for a given city.
    Output example: {"summary": "Soleado", "temperature": "23°C"}
    """
    if not API_KEY:
        return {"summary": "Clima no disponible", "temperature": None}

    # OpenWeatherMap uses 2-letter codes; map "en" → "en", "es" → "es"
    api_lang = lang if lang in {"es", "en"} else "es"

    params = {
        "q": user_city,
        "appid": API_KEY,
        "units": "metric",
        "lang": api_lang,
    }

    try:
        response = requests.get(BASE_URL, params=params, timeout=5)
        if response.status_code == 404:
            return {"summary": None, "temperature": None, "error": "city_not_found"}
        response.raise_for_status()
        data = response.json()
        summary = data.get("weather", [{}])[0].get("description", "")
        temperature = data.get("main", {}).get("temp")
        temperature_str = f"{temperature:.0f}°C" if temperature is not None else None
        return {"summary": summary, "temperature": temperature_str}
    except Exception as e:
        print(f"❌ Weather API error: {e}")
        return {"summary": None, "temperature": None, "error": "api_error"}


def get_rain_forecast(user_city: str, lang: str = "es") -> dict:
    """
    Returns peak precipitation probability for the next ~24h (8 × 3-hour periods).
    Uses max(pop) so the answer reflects "will it rain at any point today".
    Output: {"rain_probability_pct": 40} or {"error": "api_error"|"city_not_found"}
    """
    if not API_KEY:
        return {"error": "api_error"}

    api_lang = lang if lang in {"es", "en"} else "es"
    params = {
        "q": user_city,
        "appid": API_KEY,
        "units": "metric",
        "lang": api_lang,
        "cnt": 8,
    }

    try:
        response = requests.get(FORECAST_URL, params=params, timeout=5)
        if response.status_code == 404:
            return {"error": "city_not_found"}
        response.raise_for_status()
        data = response.json()
        periods = data.get("list", [])
        if not periods:
            return {"error": "api_error"}
        peak_pop = max(p.get("pop", 0.0) for p in periods)
        return {"rain_probability_pct": round(peak_pop * 100)}
    except Exception as e:
        print(f"❌ Weather forecast API error: {e}")
        return {"error": "api_error"}