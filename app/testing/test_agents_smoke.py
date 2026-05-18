"""
Agent smoke suite — verifies every agent (a) is reachable through the
deterministic router and (b) executes end-to-end into a valid AgentResult,
with external I/O (Firestore, weather, calendar, maps) mocked.

This is the regression net that proves the multi-provider calendar refactor
didn't break any agent.
"""
from unittest.mock import patch

import pytest

from app.models.agent_result import AgentResult
from app.models.parsed_message import ParsedMessage, EventReference
from app.parser.message_parser import _scan_signals
from app.router.deterministic_router import route

from app.agents.expense_agent import ExpenseAgent
from app.agents.calendar_agent import CalendarAgent
from app.agents.summary_agent import SummaryAgent
from app.agents.weather_agent import WeatherAgent
from app.agents.greeting_agent import GreetingAgent
from app.agents.ambiguity_agent import AmbiguityAgent
from app.agents.travel_agent import TravelAgent
from app.agents.list_agent import ListAgent

USER = {"phone_number": "+573001234567", "language": "es", "name": "Otto",
        "preferred_currency": "COP", "location": "Bogotá, Colombia",
        "timezone": "America/Bogota"}


def _pm(text, **kw):
    return ParsedMessage(raw_message=text, signals=_scan_signals(text), **kw)


# ── Routing: each agent is reachable ────────────────────────────────────────

def test_route_expense():
    assert isinstance(route(_pm("gasté 20000 en almuerzo", amount=20000.0)).agent,
                       ExpenseAgent)


def test_route_calendar():
    assert isinstance(route(_pm("¿qué tengo en mi calendario hoy?")).agent,
                       CalendarAgent)


def test_route_calendar_followup_no_keyword():
    parsed = ParsedMessage(raw_message="y el segundo?", signals=[],
                            event_reference=EventReference(index=1))
    assert isinstance(route(parsed).agent, CalendarAgent)


def test_route_weather():
    assert isinstance(route(_pm("¿cómo está el clima?")).agent, WeatherAgent)


def test_route_summary():
    assert isinstance(route(_pm("¿cuánto gasté esta semana?")).agent, SummaryAgent)


def test_route_travel():
    assert isinstance(route(_pm("¿a qué hora salir para mi reunión?")).agent,
                       TravelAgent)


def test_route_greeting():
    assert isinstance(route(_pm("hola")).agent, GreetingAgent)


def test_route_ambiguity_fallback():
    assert isinstance(route(_pm("asdfqwer zzz")).agent, AmbiguityAgent)


def test_route_list_save():
    parsed = ParsedMessage(raw_message="guarda esto", signals=[],
                            list_intent="save", list_item="https://x.com")
    assert isinstance(route(parsed).agent, ListAgent)


# ── Execution: each agent produces a valid AgentResult ──────────────────────

def test_greeting_executes():
    res = GreetingAgent().execute(_pm("hola"), USER)
    assert isinstance(res, AgentResult) and res.success
    assert res.data["type"] == "greeting" and res.data["response"]


def test_ambiguity_executes():
    res = AmbiguityAgent().execute(_pm("asdfqwer zzz"), USER)
    assert isinstance(res, AgentResult) and res.agent_name == "AmbiguityAgent"


@patch("app.agents.weather_agent._shared.weather_fetcher.get_rain_forecast",
       return_value={"rain_probability_pct": 10})
@patch("app.agents.weather_agent._shared.weather_fetcher.get_weather_for_today",
       return_value={"summary": "Soleado", "temperature": "24°C"})
def test_weather_executes(_mw, _mr):
    res = WeatherAgent().execute(_pm("¿cómo está el clima?"), USER)
    assert isinstance(res, AgentResult) and res.success


@patch("app.agents.summary_agent.ExpenseRepository.get_expenses_by_date_range",
       return_value=[{"amount": 1000, "currency": "COP", "category": "food"}])
def test_summary_executes(_ms):
    res = SummaryAgent().execute(_pm("¿cuánto gasté esta semana?"), USER)
    assert isinstance(res, AgentResult) and res.success


@patch("app.agents.expense_agent.ExpenseRepository.save_expense",
       return_value={"expense_id": "e1"})
def test_expense_executes(_me):
    res = ExpenseAgent().execute(
        _pm("gasté 20000 en almuerzo", amount=20000.0, currency="COP"), USER)
    assert isinstance(res, AgentResult) and res.success


@patch("app.agents.calendar_agent.get_today_events_merged",
       return_value=[{"id": "1", "summary": "Standup",
                      "start": {"dateTime": "2026-05-17T09:00:00+00:00"},
                      "end": {"dateTime": "2026-05-17T09:30:00+00:00"}}])
@patch("app.agents.calendar_agent.iter_calendar_accounts",
       return_value=[{"provider": "google", "refresh_token": "t",
                      "is_primary": True}])
def test_calendar_query_executes(_mi, _me):
    res = CalendarAgent().execute(_pm("¿qué tengo hoy en mi calendario?"), USER)
    assert isinstance(res, AgentResult) and res.success
    assert res.data["type"] == "calendar_query"
    assert res.data["event_count"] == 1


@patch("app.agents.calendar_agent.iter_calendar_accounts", return_value=[])
def test_calendar_not_connected_is_graceful(_mi):
    res = CalendarAgent().execute(_pm("¿qué tengo hoy en mi calendario?"), USER)
    assert isinstance(res, AgentResult)
    assert res.success is False
    assert res.error_message == "calendar_not_connected"


@patch("app.agents.travel_agent.skills.next_event_travel.get_today_events_merged",
       return_value=[])
@patch("app.agents.travel_agent.agent.iter_calendar_accounts",
       return_value=[{"provider": "google", "refresh_token": "t",
                      "is_primary": True}])
def test_travel_executes(_mi, _me):
    res = TravelAgent().execute(_pm("¿a qué hora salir para mi reunión?"), USER)
    assert isinstance(res, AgentResult)


@patch("app.agents.travel_agent.agent.iter_calendar_accounts", return_value=[])
def test_travel_not_connected_is_graceful(_mi):
    res = TravelAgent().execute(_pm("¿a qué hora salir para mi reunión?"), USER)
    assert isinstance(res, AgentResult) and res.success is False
    assert res.error_message == "calendar_not_connected"


@patch("app.repositories.list_repository.ListRepository.get_user_lists",
       return_value=[])
def test_list_recall_executes(_ml):
    parsed = ParsedMessage(raw_message="muéstrame mi lista de compras",
                            signals=[], list_intent="recall",
                            list_name="compras")
    res = ListAgent().execute(parsed, USER)
    assert isinstance(res, AgentResult)
