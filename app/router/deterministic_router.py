from app.models.parsed_message import ParsedMessage
from app.agents.base_agent import BaseAgent
from app.agents.expense_agent import ExpenseAgent
from app.agents.calendar_agent import CalendarAgent
from app.agents.travel_agent import TravelAgent
from app.agents.summary_agent import SummaryAgent
from app.agents.weather_agent import WeatherAgent
from app.agents.ambiguity_agent import AmbiguityAgent

# Keyword sets mirror parser/message_parser.py — kept here for routing logic
CALENDAR_KEYWORDS = {"calendario", "agenda", "reunion", "reunión", "meeting", "event", "evento", "tengo", "schedule", "have", "day", "busy"}
WEATHER_KEYWORDS  = {"clima", "weather", "lluvia", "temperatura", "temperature", "rain", "calor", "frio"}
SUMMARY_KEYWORDS  = {"resumen", "summary", "cuanto", "cuánto", "gaste", "gasté", "spent", "gastos", "expenses"}
TRAVEL_KEYWORDS   = {"llegar", "llego", "tráfico", "trafico", "traffic", "travel", "arrive", "salir", "leave"}


def route(parsed: ParsedMessage) -> BaseAgent:
    """
    Layer 2: Deterministic router. Pure logic, no LLM, no Firestore.
    Returns the correct agent instance for the given ParsedMessage.

    Priority order (no exceptions):
      1. amount present          → ExpenseAgent
      2. travel keyword          → TravelAgent
      3. weather keyword         → WeatherAgent
      4. summary keyword         → SummaryAgent   (specific money words beat generic calendar words)
      5. calendar keyword        → CalendarAgent
      6. event_reference present → CalendarAgent   (ordinal/next follow-ups with no keyword)
      7. fallback                → AmbiguityAgent
    """
    signals = set(parsed.signals)

    if parsed.amount is not None:
        return ExpenseAgent()

    if signals & TRAVEL_KEYWORDS:
        return TravelAgent()

    if signals & WEATHER_KEYWORDS:
        return WeatherAgent()

    if signals & SUMMARY_KEYWORDS:
        return SummaryAgent()

    if signals & CALENDAR_KEYWORDS:
        return CalendarAgent()

    if parsed.event_reference is not None:
        return CalendarAgent()

    return AmbiguityAgent()
