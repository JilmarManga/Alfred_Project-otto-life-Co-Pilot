"""
Integration tests for run_morning_briefing and compose_morning_insights.

run_morning_briefing(user: dict) -> bool
  - Receives a fully-resolved user dict (phone, language, name, location,
    _refresh_token already decrypted by the cron caller)
  - Returns True when WhatsApp delivered (HTTP 200), False otherwise
"""

from unittest.mock import patch, MagicMock

from app.services.morning_briefing import run_morning_briefing
from app.services.morning_brief.morning_brief_composer import compose_morning_insights
from app.models.morning_brief import MorningBriefData


def _user(phone="+1234567890", lang="en", name="Alice", location="Bogotá, Colombia", token="tok"):
    return {"phone": phone, "language": lang, "name": name, "location": location, "_refresh_token": token}


def _brief_data():
    return MorningBriefData(event_count=0, first_event=None, expense=None,
                            balance_warning=None, weather={})


# ── run_morning_briefing ────────────────────────────────────────────────────

@patch("app.services.morning_briefing.send_whatsapp_message", return_value=True)
@patch("app.services.morning_briefing.build_morning_message", return_value="Good morning Alice.")
@patch("app.services.morning_briefing.compose_morning_insights", return_value=_brief_data())
def test_returns_true_when_send_succeeds(mock_compose, mock_build, mock_send):
    result = run_morning_briefing(_user())
    assert result is True


@patch("app.services.morning_briefing.send_whatsapp_message", return_value=False)
@patch("app.services.morning_briefing.build_morning_message", return_value="Good morning Alice.")
@patch("app.services.morning_briefing.compose_morning_insights", return_value=_brief_data())
def test_returns_false_when_send_fails(mock_compose, mock_build, mock_send):
    result = run_morning_briefing(_user())
    assert result is False


@patch("app.services.morning_briefing.send_whatsapp_message", return_value=True)
@patch("app.services.morning_briefing.build_morning_message", return_value="Good morning Alice.")
@patch("app.services.morning_briefing.compose_morning_insights", return_value=_brief_data())
def test_english_language_and_name_passed_to_builder(mock_compose, mock_build, mock_send):
    run_morning_briefing(_user(lang="en", name="Alice"))
    mock_build.assert_called_once_with(
        data=mock_compose.return_value,
        language="en",
        user_name="Alice",
    )


@patch("app.services.morning_briefing.send_whatsapp_message", return_value=True)
@patch("app.services.morning_briefing.build_morning_message", return_value="Buenos días Otto.")
@patch("app.services.morning_briefing.compose_morning_insights", return_value=_brief_data())
def test_spanish_language_and_name_passed_to_builder(mock_compose, mock_build, mock_send):
    run_morning_briefing(_user(lang="es", name="Otto", phone="+573001234567"))
    mock_build.assert_called_once_with(
        data=mock_compose.return_value,
        language="es",
        user_name="Otto",
    )


@patch("app.services.morning_briefing.send_whatsapp_message", return_value=True)
@patch("app.services.morning_briefing.build_morning_message", return_value="message")
@patch("app.services.morning_briefing.compose_morning_insights", return_value=_brief_data())
def test_send_called_with_phone_and_message(mock_compose, mock_build, mock_send):
    run_morning_briefing(_user(phone="+573043775520"))
    mock_send.assert_called_once_with(to="+573043775520", message="message")


# ── compose_morning_insights ────────────────────────────────────────────────

@patch("app.services.morning_brief.morning_brief_composer.get_rain_forecast",
       return_value={"error": "api_error"})
@patch("app.services.morning_brief.morning_brief_composer.estimate_travel_info",
       return_value=(None, None))
@patch("app.services.morning_brief.morning_brief_composer.get_weather_for_today",
       return_value={"summary": "Sunny", "temperature": "28°C"})
@patch("app.services.morning_brief.morning_brief_composer.get_today_events_for_user",
       return_value=[])
def test_composer_uses_user_location_for_weather(mock_events, mock_weather, mock_travel, mock_rain):
    user = _user(location="Cartagena, Colombia", lang="es")
    compose_morning_insights(user)
    mock_weather.assert_called_once_with(user_city="Cartagena, Colombia", lang="es")


@patch("app.services.morning_brief.morning_brief_composer.get_rain_forecast",
       return_value={"error": "api_error"})
@patch("app.services.morning_brief.morning_brief_composer.estimate_travel_info",
       return_value=(None, None))
@patch("app.services.morning_brief.morning_brief_composer.get_weather_for_today",
       return_value={"summary": "Rainy", "temperature": "15°C"})
@patch("app.services.morning_brief.morning_brief_composer.get_today_events_for_user",
       return_value=[])
def test_composer_passes_language_to_weather(mock_events, mock_weather, mock_travel, mock_rain):
    user = _user(location="Buenos Aires, Argentina", lang="en")
    compose_morning_insights(user)
    mock_weather.assert_called_once_with(user_city="Buenos Aires, Argentina", lang="en")


@patch("app.services.morning_brief.morning_brief_composer.get_rain_forecast",
       return_value={"rain_probability_pct": 40})
@patch("app.services.morning_brief.morning_brief_composer.estimate_travel_info",
       return_value=(None, None))
@patch("app.services.morning_brief.morning_brief_composer.get_weather_for_today",
       return_value={"summary": "Partly cloudy", "temperature": "22°C"})
@patch("app.services.morning_brief.morning_brief_composer.get_today_events_for_user",
       return_value=[])
def test_composer_merges_rain_probability_into_weather(mock_events, mock_weather, mock_travel, mock_rain):
    data = compose_morning_insights(_user())
    assert data.weather.get("rain_probability_pct") == 40
