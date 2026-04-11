import re
from app.agents.base_agent import BaseAgent
from app.models.parsed_message import ParsedMessage
from app.models.agent_result import AgentResult
from app.services.weather.weather_service import get_weather_for_today


def _extract_city_from_message(text: str) -> str | None:
    """Extract an explicitly mentioned city from the message, e.g. 'clima en Bogota'."""
    # Match "en <City>" or "in <City>" — capture the next 1-2 capitalized words
    match = re.search(r'\b(?:en|in)\s+([A-ZÁÉÍÓÚÑ][a-záéíóúñA-ZÁÉÍÓÚÑ]+(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñA-ZÁÉÍÓÚÑ]+)?)', text)
    if match:
        return match.group(1).strip()
    return None


class WeatherAgent(BaseAgent):

    def execute(self, parsed: ParsedMessage, user: dict) -> AgentResult:
        try:
            default_city = user.get("location", "Bogotá, Colombia")
            lang = user.get("language", "es")

            # If user explicitly names a city in their message, use that
            mentioned_city = _extract_city_from_message(parsed.raw_message or "")
            city = mentioned_city if mentioned_city else default_city

            weather = get_weather_for_today(user_city=city, lang=lang)

            if weather.get("error") == "city_not_found":
                # Let LLM format a helpful "city not found" message
                return AgentResult(
                    agent_name="WeatherAgent",
                    success=True,
                    data={
                        "city": city,
                        "city_not_found": True,
                    },
                )

            if weather.get("error") == "api_error" or weather.get("summary") is None:
                return AgentResult(
                    agent_name="WeatherAgent",
                    success=False,
                    error_message="weather_api_unavailable",
                )

            return AgentResult(
                agent_name="WeatherAgent",
                success=True,
                data={
                    "city": city,
                    "summary": weather.get("summary"),
                    "temperature": weather.get("temperature"),
                },
            )

        except Exception as e:
            return AgentResult(
                agent_name="WeatherAgent",
                success=False,
                error_message=str(e),
            )
