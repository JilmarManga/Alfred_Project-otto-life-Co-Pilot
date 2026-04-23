from app.models.parsed_message import ParsedMessage
from app.agents.base_agent import BaseAgent
from app.agents.expense_agent import ExpenseAgent
from app.agents.calendar_agent import CalendarAgent
from app.agents.travel_agent import TravelAgent
from app.agents.summary_agent import SummaryAgent
from app.agents.weather_agent import WeatherAgent
from app.agents.ambiguity_agent import AmbiguityAgent
from app.agents.greeting_agent import GreetingAgent

# Keyword sets mirror parser/message_parser.py — kept here for routing logic
CALENDAR_KEYWORDS  = {"calendario", "calendar", "agenda", "reunion", "reunión", "meeting", "event", "evento", "tengo", "schedule", "have", "day", "busy"}
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
    "programa", "programar",
    "programa una", "programa un", "programar una", "programar un",
    "nueva reunión", "nuevo evento",
    # English
    "add event", "add a meeting", "add an event",
    "create event", "create meeting", "create a meeting", "create an event",
    "schedule a", "schedule an", "schedule my",
    "program a", "program an", "program my",
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


def route(parsed: ParsedMessage) -> BaseAgent:
    """
    Layer 2: Deterministic router. Pure logic, no LLM, no Firestore.
    Returns the correct agent instance for the given ParsedMessage.

    Priority order (no exceptions):
      1. reminder toggle phrase        → CalendarAgent    (settings — bypasses other signals)
      2. amount present                → ExpenseAgent
      3. travel keyword                → TravelAgent
      4. weather keyword               → WeatherAgent
      5. summary keyword               → SummaryAgent    (specific money words beat generic calendar words)
      6. calendar keyword              → CalendarAgent
      7. create keyword                → CalendarAgent    (event creation intent with no calendar noun)
      8. event_title + event_start set → CalendarAgent    (parser extracted a new event but no keyword matched)
      9. event_reference present       → CalendarAgent    (ordinal/next follow-ups with no keyword)
     10. greeting keyword              → GreetingAgent
     11. gratitude keyword             → GreetingAgent
     12. fallback                      → AmbiguityAgent
    """
    signals = set(parsed.signals)

    # Reminder settings jump to the front — otherwise "disable reminders" could
    # hit GreetingAgent/Ambiguity since it has no calendar noun.
    if signals & REMINDER_TOGGLE_KEYWORDS:
        return CalendarAgent()

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

    # Parser extracted a new event (title + start) but no CREATE/CALENDAR keyword
    # matched (e.g. "Add to the calendar a medical check tomorrow 7 am"). Route to
    # CalendarAgent which will run _handle_clarify_creation or _handle_creation.
    if parsed.event_title and parsed.event_start:
        return CalendarAgent()

    if parsed.event_reference is not None:
        return CalendarAgent()

    if signals & GREETING_KEYWORDS:
        return GreetingAgent()

    if signals & GRATITUDE_KEYWORDS:
        return GreetingAgent()

    return AmbiguityAgent()
