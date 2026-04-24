from typing import Optional

from app.models.parsed_message import ParsedMessage
from app.agents.base_agent import BaseAgent
from app.agents.expense_agent import ExpenseAgent
from app.agents.calendar_agent import CalendarAgent
from app.agents.travel_agent import TravelAgent
from app.agents.summary_agent import SummaryAgent
from app.agents.weather_agent import WeatherAgent
from app.agents.ambiguity_agent import AmbiguityAgent
from app.agents.greeting_agent import GreetingAgent
from app.agents.list_agent import ListAgent
from app.router.route_decision import Disambiguation, RouteDecision

# Keyword sets mirror parser/message_parser.py — kept here for routing logic
CALENDAR_KEYWORDS  = {"calendario", "agenda", "reunion", "reunión", "meeting", "event", "evento", "tengo", "schedule", "have", "day", "busy"}
WEATHER_KEYWORDS   = {"clima", "weather", "lluvia", "temperatura", "temperature", "rain", "calor", "frio"}
SUMMARY_KEYWORDS   = {"resumen", "summary", "cuanto", "cuánto", "gaste", "gasté", "spent", "gastos", "expenses",
                       "wasted", "waste", "spend", "money", "dinero", "plata", "gastado"}
TRAVEL_KEYWORDS    = {"llegar", "llego", "tráfico", "trafico", "traffic", "travel", "arrive", "salir", "leave"}
GREETING_KEYWORDS  = {"hola", "hello", "hey", "buenos días", "buenos dias",
                       "good morning", "buenas tardes", "good afternoon",
                       "buenas noches", "good evening", "buenas", "que tal", "qué tal"}
GRATITUDE_KEYWORDS = {"gracias", "thanks", "thank you", "thankss", "thanx", "grax", "tks"}
CREATE_KEYWORDS    = {
    # Spanish
    "agendar", "agendalo", "agéndalo",
    "agenda una", "agenda un", "agenda el", "agenda mi",
    "crea una", "crea un", "crea el", "crea mi", "crea la",
    "crear una", "crear un", "crear el", "crear mi", "crear la",
    "agrega", "agrega una", "agrega un", "agrega el", "agrega mi",
    "agregar al calendario", "añade al calendario", "añadir al calendario",
    "programa una", "programa un", "programar una", "programar un",
    "nueva reunión", "nuevo evento",
    # English
    "add event", "add a meeting", "add an event",
    "create event", "create meeting", "create a meeting", "create an event",
    "schedule a", "schedule an", "schedule my",
    "book a", "book an", "book me",
    "set up a meeting", "new meeting", "new event",
    "put it on my calendar", "add to my calendar",
}
REMINDER_OFF_KEYWORDS = {
    # Spanish
    "recordatorios off", "desactivar recordatorios", "desactiva recordatorios",
    "desactivar los recordatorios", "quitar recordatorios", "quita recordatorios",
    "sin recordatorios", "apaga recordatorios", "apagar recordatorios",
    # English
    "turn off reminders", "stop reminders", "disable reminders",
    "mute reminders", "no more reminders",
}
REMINDER_ON_KEYWORDS = {
    # Spanish
    "recordatorios on", "activar recordatorios", "activa recordatorios",
    "activar los recordatorios", "reactivar recordatorios",
    "enciende recordatorios", "encender recordatorios",
    # English
    "turn on reminders", "enable reminders", "start reminders",
    "resume reminders",
}
REMINDER_TOGGLE_KEYWORDS = REMINDER_OFF_KEYWORDS | REMINDER_ON_KEYWORDS


def _pick_keyword_agent(parsed: ParsedMessage, signals: set) -> Optional[BaseAgent]:
    """Existing keyword-based priority chain, unchanged in order and effect.

    Returns a concrete agent when a rule fires, or None when nothing matches
    (caller falls back to AmbiguityAgent). Extracted into its own function so
    `route()` can compare the keyword-match to a separate list-pattern match.
    """
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

    if signals & CREATE_KEYWORDS:
        return CalendarAgent()

    if parsed.event_reference is not None:
        return CalendarAgent()

    if signals & GREETING_KEYWORDS:
        return GreetingAgent()

    if signals & GRATITUDE_KEYWORDS:
        return GreetingAgent()

    return None


def route(parsed: ParsedMessage, *, skip_list: bool = False) -> RouteDecision:
    """
    Layer 2: Deterministic router. Pure logic, no LLM, no Firestore.
    Returns a RouteDecision carrying either the chosen agent or a
    Disambiguation when two candidates match.

    Priority order (no exceptions):
      0. reminder toggle phrase  → CalendarAgent    (settings — wins over lists too)
      1. amount present          → ExpenseAgent
      2. travel keyword          → TravelAgent
      3. weather keyword         → WeatherAgent
      4. summary keyword         → SummaryAgent    (specific money words beat generic calendar words)
      5. calendar keyword        → CalendarAgent
      6. create keyword          → CalendarAgent    (event creation intent with no calendar noun)
      7. event_reference present → CalendarAgent    (ordinal/next follow-ups with no keyword)
      8. greeting keyword        → GreetingAgent
      9. gratitude keyword       → GreetingAgent
     10. fallback                → AmbiguityAgent

    `skip_list`: reserved for the Gate-5 awaiting_disambiguation branch.
    When the user picks the non-list candidate, the gate re-calls this
    router with skip_list=True so the list-match logic is bypassed and the
    original keyword agent is returned cleanly. ListAgent wiring is added
    in a later commit; the parameter is accepted now so the signature is
    stable from day one.
    """
    signals = set(parsed.signals)

    # Reminder settings jump to the front — otherwise "disable reminders" could
    # hit GreetingAgent/Ambiguity since it has no calendar noun. Reminder
    # toggles also override ListAgent (settings > lists).
    if signals & REMINDER_TOGGLE_KEYWORDS:
        return RouteDecision(agent=CalendarAgent())

    keyword_agent = _pick_keyword_agent(parsed, signals)
    list_match = (not skip_list) and ListAgent.matches(parsed)

    if list_match:
        # Only true functional agents trigger disambiguation. Greeting and the
        # ambiguity fallback (keyword_agent is None) lose to ListAgent outright.
        if keyword_agent is None or isinstance(keyword_agent, GreetingAgent):
            return RouteDecision(agent=ListAgent())
        return RouteDecision(
            disambiguation=Disambiguation(
                candidates=["ListAgent", keyword_agent.__class__.__name__],
            ),
        )

    return RouteDecision(agent=keyword_agent or AmbiguityAgent())
