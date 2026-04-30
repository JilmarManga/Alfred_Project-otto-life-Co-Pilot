"""
Tests: Calendar routing fixes

Verifies that the three live-test failures now route correctly,
and that summary vs calendar collision is handled safely.
"""

from unittest.mock import patch, MagicMock
import pytest

from app.models.parsed_message import ParsedMessage, EventReference
from app.router.deterministic_router import route
from app.agents.calendar_agent import CalendarAgent
from app.agents.summary_agent import SummaryAgent
from app.agents.expense_agent import ExpenseAgent
from app.agents.weather_agent import WeatherAgent
from app.agents.travel_agent import TravelAgent
from app.parser.message_parser import _parse_event_reference, _scan_signals


# ---------------------------------------------------------------------------
# Router tests — the three failures from live WhatsApp testing
# ---------------------------------------------------------------------------

def test_what_about_the_second_routes_to_calendar():
    """'What about the second?' has no calendar keyword but has event_reference."""
    parsed = ParsedMessage(
        raw_message="What about the second?",
        signals=[],
        event_reference=EventReference(index=1),
    )
    assert isinstance(route(parsed).agent, CalendarAgent)


def test_how_is_my_day_today_routes_to_calendar():
    """'How is my day today?' matches 'day' keyword."""
    parsed = ParsedMessage(
        raw_message="How is my day today?",
        signals=_scan_signals("How is my day today?"),
    )
    assert isinstance(route(parsed).agent, CalendarAgent)


def test_what_do_i_have_today_routes_to_calendar():
    """'What do I have today?' matches 'have' keyword."""
    parsed = ParsedMessage(
        raw_message="What do I have today?",
        signals=_scan_signals("What do I have today?"),
    )
    assert isinstance(route(parsed).agent, CalendarAgent)


# ---------------------------------------------------------------------------
# Router tests — additional calendar coverage
# ---------------------------------------------------------------------------

def test_que_tengo_hoy_routes_to_calendar():
    """Regression: existing Spanish calendar query still works."""
    parsed = ParsedMessage(
        raw_message="Qué tengo hoy?",
        signals=_scan_signals("Qué tengo hoy?"),
    )
    assert isinstance(route(parsed).agent, CalendarAgent)


def test_ordinal_followup_spanish_routes_to_calendar():
    """'Y el tercero?' has event_reference, no keyword."""
    parsed = ParsedMessage(
        raw_message="Y el tercero?",
        signals=[],
        event_reference=EventReference(index=2),
    )
    assert isinstance(route(parsed).agent, CalendarAgent)


def test_digit_ordinal_routes_to_calendar():
    """'Tell me about the 7th' — regex ordinal fallback."""
    parsed = ParsedMessage(
        raw_message="Tell me about the 7th",
        signals=[],
        event_reference=EventReference(index=6),
    )
    assert isinstance(route(parsed).agent, CalendarAgent)


# ---------------------------------------------------------------------------
# Summary vs calendar collision — the safety net
# ---------------------------------------------------------------------------

def test_have_spent_routes_to_summary_not_calendar():
    """'How much have I spent this week?' — summary keywords beat 'have'."""
    parsed = ParsedMessage(
        raw_message="How much have I spent this week?",
        signals=_scan_signals("How much have I spent this week?"),
    )
    assert isinstance(route(parsed).agent, SummaryAgent)


def test_cuanto_gaste_routes_to_summary():
    """Regression: Spanish summary query unaffected."""
    parsed = ParsedMessage(
        raw_message="Cuánto gasté esta semana?",
        signals=_scan_signals("Cuánto gasté esta semana?"),
    )
    assert isinstance(route(parsed).agent, SummaryAgent)


# ---------------------------------------------------------------------------
# Parser tests — ordinal detection
# ---------------------------------------------------------------------------

def test_parse_second_returns_index_1():
    assert _parse_event_reference("What about the second?") == EventReference(index=1)


def test_parse_tercero_returns_index_2():
    assert _parse_event_reference("Y el tercero?") == EventReference(index=2)


def test_parse_7th_regex_returns_index_6():
    assert _parse_event_reference("Tell me about the 7th") == EventReference(index=6)


def test_parse_11th_regex_returns_index_10():
    assert _parse_event_reference("What about the 11th event?") == EventReference(index=10)


def test_parse_next_returns_time_reference():
    assert _parse_event_reference("What's my next event?") == EventReference(time_reference="next")


def test_parse_siguiente_returns_time_reference():
    assert _parse_event_reference("Cuál es el siguiente?") == EventReference(time_reference="next")


def test_parse_no_reference_returns_none():
    assert _parse_event_reference("How is the weather today?") is None


def test_parse_bare_digit_one_returns_event_reference():
    """'the 2 one' — bare digit + one pattern."""
    assert _parse_event_reference("Tell me about the 2 one") == EventReference(index=1)


def test_parse_bare_digit_uno_returns_event_reference():
    """'el 5 uno' — bare digit + uno pattern."""
    assert _parse_event_reference("el 5 uno") == EventReference(index=4)


# ---------------------------------------------------------------------------
# Amount suppression when event_reference is present
# ---------------------------------------------------------------------------

from app.parser.message_parser import _heuristic_parse

def test_second_one_amount_is_suppressed():
    """'Give me details of the second one' must NOT produce amount=1."""
    parsed = _heuristic_parse("Give me details of the second one please")
    assert parsed.amount is None
    assert parsed.event_reference == EventReference(index=1)


def test_expense_without_ordinal_preserves_amount():
    """'Gasté uno en café' has no ordinal — amount=1 must be preserved."""
    parsed = _heuristic_parse("Gasté uno en café")
    assert parsed.amount == 1.0
    assert parsed.event_reference is None


def test_expense_200_no_ordinal_preserves_amount():
    """'200 dolares en comida' — no ordinal, amount preserved."""
    parsed = _heuristic_parse("200 dolares en comida")
    assert parsed.amount == 200.0
    assert parsed.event_reference is None
