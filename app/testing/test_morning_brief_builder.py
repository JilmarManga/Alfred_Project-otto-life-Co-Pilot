"""
Tests for Fix 4: Weather label respects user language.

Before the fix, the weather label was hardcoded to "Clima:" regardless of language.
After the fix, Spanish users see "Clima:" and English users see "Weather:".

build_morning_message is a pure function — no mocking needed.
"""

from app.services.morning_brief.message_builder import build_morning_message
from app.models.morning_brief import MorningBriefData


def make_data(weather: dict = None) -> MorningBriefData:
    return MorningBriefData(
        event_count=0,
        first_event=None,
        expense=None,
        balance_warning=None,
        weather=weather or {},
    )


def test_weather_label_is_spanish_for_es_users():
    data = make_data({"summary": "Soleado", "temperature": "25°C"})
    message = build_morning_message(data, language="es", user_name=None)
    assert "Clima:" in message
    assert "Weather:" not in message


def test_weather_label_is_english_for_en_users():
    data = make_data({"summary": "Sunny", "temperature": "25°C"})
    message = build_morning_message(data, language="en", user_name=None)
    assert "Weather:" in message
    assert "Clima:" not in message


def test_weather_includes_temperature():
    data = make_data({"summary": "Partly cloudy", "temperature": "18°C"})
    message = build_morning_message(data, language="en", user_name=None)
    assert "18°C" in message


def test_no_weather_data_produces_no_weather_line():
    data = make_data({})
    message = build_morning_message(data, language="es", user_name=None)
    assert "Clima:" not in message
    assert "Weather:" not in message


def test_greeting_includes_user_name_spanish():
    data = make_data()
    message = build_morning_message(data, language="es", user_name="Jilmar")
    assert "Jilmar" in message
    assert "Buenos días" in message


def test_greeting_includes_user_name_english():
    data = make_data()
    message = build_morning_message(data, language="en", user_name="Alice")
    assert "Alice" in message
    assert "Good morning" in message
