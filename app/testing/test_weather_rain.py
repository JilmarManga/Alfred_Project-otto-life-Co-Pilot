"""
Smoke + E2E tests for the weather rain enhancements.

Coverage:
1. Keyword routing — va a llover, is it going to rain, will it be hot, etc.
2. WeatherAgent skill dispatch — rain signals → rain_check, others → current_conditions
3. Skill output shape — both skills return expected data keys
4. Morning brief builder — rain_probability_pct renders in the brief message
5. Forecast graceful degrade — forecast_unavailable=True flows through without crash

No real API calls. All external services are mocked.
"""

from unittest.mock import patch, MagicMock

from app.parser.message_parser import _scan_signals, WEATHER_KEYWORDS
from app.router.deterministic_router import route, WEATHER_KEYWORDS as ROUTER_WEATHER_KEYWORDS
from app.agents.weather_agent import WeatherAgent
from app.agents.weather_agent.agent import _RAIN_SIGNALS
from app.agents.weather_agent.skill_context import SkillContext
from app.agents.weather_agent.skills.current_conditions import CurrentConditionsSkill
from app.agents.weather_agent.skills.rain_check import RainCheckSkill
from app.models.parsed_message import ParsedMessage
from app.services.morning_brief.message_builder import build_morning_message
from app.models.morning_brief import MorningBriefData


# ── Helpers ────────────────────────────────────────────────────────────────

def make_parsed(raw: str) -> ParsedMessage:
    return ParsedMessage(
        raw_message=raw,
        signals=_scan_signals(raw),
        amount=None,
        currency=None,
    )


def make_user(lang: str = "es") -> dict:
    return {"language": lang, "location": "Bogotá, Colombia", "name": "Test"}


def make_brief(weather: dict) -> MorningBriefData:
    return MorningBriefData(
        event_count=0,
        first_event=None,
        expense=None,
        balance_warning=None,
        weather=weather,
    )


# ── 1. Keyword sets are in sync between parser and router ──────────────────

def test_parser_and_router_weather_keywords_are_identical():
    from app.parser.message_parser import WEATHER_KEYWORDS as PARSER_WEATHER_KEYWORDS
    assert PARSER_WEATHER_KEYWORDS == ROUTER_WEATHER_KEYWORDS


# ── 2. New keywords appear in both sets ────────────────────────────────────

def test_rain_verbs_in_weather_keywords():
    for kw in ("llover", "lloverá", "llueve", "raining"):
        assert kw in WEATHER_KEYWORDS, f"Missing keyword: {kw}"


def test_hot_phrases_in_weather_keywords():
    for kw in ("be hot", "is it hot", "too hot", "so hot", "how hot", "getting hot"):
        assert kw in WEATHER_KEYWORDS, f"Missing keyword: {kw}"


# ── 3. Routing: rain queries reach WeatherAgent ────────────────────────────

def test_va_a_llover_routes_to_weather():
    parsed = make_parsed("va a llover hoy?")
    assert type(route(parsed).agent).__name__ == "WeatherAgent"


def test_llueve_routes_to_weather():
    parsed = make_parsed("llueve ahorita?")
    assert type(route(parsed).agent).__name__ == "WeatherAgent"


def test_is_it_going_to_rain_routes_to_weather():
    parsed = make_parsed("is it going to rain today?")
    assert type(route(parsed).agent).__name__ == "WeatherAgent"


def test_will_it_be_hot_routes_to_weather():
    parsed = make_parsed("will it be hot today?")
    assert type(route(parsed).agent).__name__ == "WeatherAgent"


def test_como_va_el_clima_routes_to_weather():
    parsed = make_parsed("como va a estar el clima hoy")
    assert type(route(parsed).agent).__name__ == "WeatherAgent"


def test_va_a_hacer_calor_routes_to_weather():
    parsed = make_parsed("Va a hacer calor hoy?")
    assert type(route(parsed).agent).__name__ == "WeatherAgent"


# ── 4. Routing: hot phrases do NOT break hotel/shot ────────────────────────

def test_book_a_hotel_routes_to_calendar_not_weather():
    parsed = make_parsed("book a hotel room")
    agent_name = type(route(parsed).agent).__name__
    assert agent_name != "WeatherAgent", "book a hotel should not route to WeatherAgent"


# ── 5. WeatherAgent skill dispatch ────────────────────────────────────────

def test_lluvia_signal_picks_rain_check():
    parsed = make_parsed("va a llover hoy?")
    agent = WeatherAgent()
    assert agent._pick_skill(parsed) == "rain_check"


def test_llueve_signal_picks_rain_check():
    parsed = make_parsed("llueve mucho hoy?")
    agent = WeatherAgent()
    assert agent._pick_skill(parsed) == "rain_check"


def test_rain_signal_picks_rain_check():
    parsed = make_parsed("is it going to rain?")
    agent = WeatherAgent()
    assert agent._pick_skill(parsed) == "rain_check"


def test_clima_signal_picks_current_conditions():
    parsed = make_parsed("como va el clima hoy")
    agent = WeatherAgent()
    assert agent._pick_skill(parsed) == "current_conditions"


def test_hot_phrase_picks_current_conditions():
    parsed = make_parsed("will it be hot today?")
    agent = WeatherAgent()
    assert agent._pick_skill(parsed) == "current_conditions"


# ── 6. Skill output shape ─────────────────────────────────────────────────

MOCK_CURRENT = {"summary": "partly cloudy", "temperature": "22°C"}
MOCK_FORECAST = {"rain_probability_pct": 35}


@patch("app.agents.weather_agent._shared.weather_fetcher.get_rain_forecast", return_value=MOCK_FORECAST)
@patch("app.agents.weather_agent._shared.weather_fetcher.get_weather_for_today", return_value=MOCK_CURRENT)
def test_current_conditions_returns_expected_keys(mock_weather, mock_forecast):
    ctx = SkillContext(user=make_user(), inbound_text="como va el clima hoy")
    result = CurrentConditionsSkill().execute(ctx)

    assert result.success is True
    assert result.data["type"] == "weather_general"
    assert result.data["summary"] == "partly cloudy"
    assert result.data["temperature"] == "22°C"
    assert result.data["rain_probability_pct"] == 35
    assert result.data["forecast_unavailable"] is False


@patch("app.agents.weather_agent._shared.weather_fetcher.get_rain_forecast", return_value=MOCK_FORECAST)
@patch("app.agents.weather_agent._shared.weather_fetcher.get_weather_for_today", return_value=MOCK_CURRENT)
def test_rain_check_returns_expected_keys(mock_weather, mock_forecast):
    ctx = SkillContext(user=make_user(), inbound_text="va a llover hoy?")
    result = RainCheckSkill().execute(ctx)

    assert result.success is True
    assert result.data["type"] == "weather_rain_check"
    assert result.data["rain_probability_pct"] == 35
    assert result.data["forecast_unavailable"] is False


@patch("app.agents.weather_agent._shared.weather_fetcher.get_rain_forecast", return_value={"error": "api_error"})
@patch("app.agents.weather_agent._shared.weather_fetcher.get_weather_for_today", return_value=MOCK_CURRENT)
def test_forecast_failure_sets_unavailable_flag(mock_weather, mock_forecast):
    ctx = SkillContext(user=make_user(), inbound_text="va a llover hoy?")
    result = RainCheckSkill().execute(ctx)

    assert result.success is True
    assert result.data["forecast_unavailable"] is True
    assert result.data["rain_probability_pct"] is None
    assert result.data["summary"] == "partly cloudy"


@patch("app.agents.weather_agent._shared.weather_fetcher.get_rain_forecast", return_value=MOCK_FORECAST)
@patch("app.agents.weather_agent._shared.weather_fetcher.get_weather_for_today",
       return_value={"error": "city_not_found"})
def test_city_not_found_returns_success_with_flag(mock_weather, mock_forecast):
    ctx = SkillContext(user=make_user(), inbound_text="clima en Narnia")
    result = CurrentConditionsSkill().execute(ctx)

    assert result.success is True
    assert result.data.get("city_not_found") is True


# ── 7. Morning brief builder renders rain probability ─────────────────────

def test_morning_brief_includes_rain_pct_in_spanish():
    data = make_brief({"summary": "Nublado", "temperature": "18°C", "rain_probability_pct": 60})
    msg = build_morning_message(data, language="es")
    assert "60%" in msg
    assert "lluvia" in msg.lower()


def test_morning_brief_includes_rain_pct_in_english():
    data = make_brief({"summary": "Cloudy", "temperature": "18°C", "rain_probability_pct": 60})
    msg = build_morning_message(data, language="en")
    assert "60%" in msg
    assert "rain" in msg.lower()


def test_morning_brief_omits_rain_pct_when_absent():
    data = make_brief({"summary": "Sunny", "temperature": "25°C"})
    msg = build_morning_message(data, language="es")
    assert "%" not in msg


def test_morning_brief_omits_rain_pct_when_none():
    data = make_brief({"summary": "Sunny", "temperature": "25°C", "rain_probability_pct": None})
    msg = build_morning_message(data, language="es")
    assert "%" not in msg
