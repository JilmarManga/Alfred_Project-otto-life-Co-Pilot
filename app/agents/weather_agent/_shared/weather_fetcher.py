from app.services.weather.weather_service import get_weather_for_today, get_rain_forecast


def fetch_full_weather(city: str, lang: str) -> dict:
    """
    Fetch current conditions + precipitation forecast for a city.

    Returns a dict with:
      summary, temperature          — from current-weather endpoint (always present on success)
      rain_probability_pct          — peak pop × 100 across next 24h; None if forecast failed
      forecast_unavailable          — True when forecast call failed
      error                         — "city_not_found" | "api_error" if current-weather failed
    """
    current = get_weather_for_today(user_city=city, lang=lang)

    if current.get("error"):
        return current

    result = {
        "summary": current.get("summary"),
        "temperature": current.get("temperature"),
        "rain_probability_pct": None,
        "forecast_unavailable": False,
    }

    forecast = get_rain_forecast(user_city=city, lang=lang)
    if forecast.get("error"):
        result["forecast_unavailable"] = True
    else:
        result["rain_probability_pct"] = forecast.get("rain_probability_pct")

    return result
