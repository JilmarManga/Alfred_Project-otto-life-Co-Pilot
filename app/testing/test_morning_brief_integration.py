"""
Tests for Fixes 2 + 3: Morning brief uses Firestore user data.

Before the fix:
  - language was hardcoded to "es"
  - user_name was always None
  - location came from in-memory store (always empty on startup → Bogotá fallback)

After the fix:
  - run_morning_briefing fetches the user from Firestore
  - language, name, and location all flow from Firestore to the composer and builder
"""

from unittest.mock import patch, MagicMock
from app.services.morning_briefing import run_morning_briefing
from app.services.morning_brief.morning_brief_composer import compose_morning_insights
from app.models.morning_brief import MorningBriefData


def make_brief_data() -> MorningBriefData:
    return MorningBriefData(
        event_count=0,
        first_event=None,
        expense=None,
        balance_warning=None,
        weather={},
    )


# ── Tests for run_morning_briefing ──────────────────────────────────────────

@patch("app.services.morning_briefing.send_whatsapp_message")
@patch("app.services.morning_briefing.build_morning_message", return_value="Good morning Alice.")
@patch("app.services.morning_briefing.compose_morning_insights")
@patch("app.services.morning_briefing.UserRepository.get_user")
def test_english_user_language_and_name_passed_to_builder(mock_get_user, mock_compose, mock_build, mock_send):
    mock_get_user.return_value = {"language": "en", "name": "Alice", "location": "New York"}
    mock_compose.return_value = make_brief_data()

    run_morning_briefing("+1234567890")

    mock_build.assert_called_once_with(
        data=mock_compose.return_value,
        language="en",
        user_name="Alice",
    )


@patch("app.services.morning_briefing.send_whatsapp_message")
@patch("app.services.morning_briefing.build_morning_message", return_value="Buenos días Otto.")
@patch("app.services.morning_briefing.compose_morning_insights")
@patch("app.services.morning_briefing.UserRepository.get_user")
def test_spanish_user_language_and_name_passed_to_builder(mock_get_user, mock_compose, mock_build, mock_send):
    mock_get_user.return_value = {"language": "es", "name": "Otto", "location": "Medellín, Colombia"}
    mock_compose.return_value = make_brief_data()

    run_morning_briefing("+573001234567")

    mock_build.assert_called_once_with(
        data=mock_compose.return_value,
        language="es",
        user_name="Otto",
    )


@patch("app.services.morning_briefing.send_whatsapp_message")
@patch("app.services.morning_briefing.build_morning_message", return_value="Buenos días.")
@patch("app.services.morning_briefing.compose_morning_insights")
@patch("app.services.morning_briefing.UserRepository.get_user")
def test_user_not_found_in_firestore_uses_safe_defaults(mock_get_user, mock_compose, mock_build, mock_send):
    mock_get_user.return_value = None
    mock_compose.return_value = make_brief_data()

    run_morning_briefing("+0000000000")

    mock_build.assert_called_once_with(
        data=mock_compose.return_value,
        language="es",
        user_name=None,
    )
    mock_compose.assert_called_once_with("+0000000000", user_location="Bogotá, Colombia", lang="es")


@patch("app.services.morning_briefing.send_whatsapp_message")
@patch("app.services.morning_briefing.build_morning_message", return_value="message")
@patch("app.services.morning_briefing.compose_morning_insights")
@patch("app.services.morning_briefing.UserRepository.get_user")
def test_firestore_location_is_passed_to_composer(mock_get_user, mock_compose, mock_build, mock_send):
    mock_get_user.return_value = {"language": "en", "name": "Alice", "location": "San Francisco, CA"}
    mock_compose.return_value = make_brief_data()

    run_morning_briefing("+1234567890")

    mock_compose.assert_called_once_with("+1234567890", user_location="San Francisco, CA", lang="en")


# ── Tests for compose_morning_insights: location flows to weather API ───────

@patch("app.services.morning_brief.morning_brief_composer.get_today_events", return_value=[])
@patch("app.services.morning_brief.morning_brief_composer.get_weather_for_today")
def test_weather_api_called_with_firestore_location(mock_weather, mock_events):
    mock_weather.return_value = {"summary": "Sunny", "temperature": "28°C"}

    compose_morning_insights("user_123", user_location="Cartagena, Colombia")

    mock_weather.assert_called_once_with(user_city="Cartagena, Colombia", lang="es")


@patch("app.services.morning_brief.morning_brief_composer.get_today_events", return_value=[])
@patch("app.services.morning_brief.morning_brief_composer.get_weather_for_today")
def test_weather_api_not_using_bogota_for_non_bogota_user(mock_weather, mock_events):
    mock_weather.return_value = {"summary": "Rainy", "temperature": "15°C"}

    compose_morning_insights("user_456", user_location="Buenos Aires, Argentina")

    mock_weather.assert_called_once_with(user_city="Buenos Aires, Argentina", lang="es")
    # Verify Bogotá fallback was NOT used
    call_city = mock_weather.call_args[1]["user_city"]
    assert call_city != "Bogotá, Colombia"
