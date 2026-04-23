from app.agents.weather_agent.skill_context import SkillContext, SkillResult
from app.agents.weather_agent.skills.base import WeatherSkill
from app.agents.weather_agent._shared.city_resolver import resolve_city
from app.agents.weather_agent._shared.weather_fetcher import fetch_full_weather


class RainCheckSkill(WeatherSkill):
    """Rain-specific query: leads with precipitation probability."""

    name = "rain_check"

    def execute(self, ctx: SkillContext) -> SkillResult:
        city = resolve_city(ctx.inbound_text, ctx.user)
        lang = ctx.user.get("language", "es")

        weather = fetch_full_weather(city=city, lang=lang)

        if weather.get("error") == "city_not_found":
            return SkillResult(
                success=True,
                data={"type": "weather_rain_check", "city": city, "city_not_found": True},
            )

        if weather.get("error") == "api_error" or weather.get("summary") is None:
            return SkillResult(success=False, error_message="weather_api_unavailable")

        return SkillResult(
            success=True,
            data={
                "type": "weather_rain_check",
                "city": city,
                "summary": weather["summary"],
                "temperature": weather["temperature"],
                "rain_probability_pct": weather["rain_probability_pct"],
                "forecast_unavailable": weather["forecast_unavailable"],
            },
        )
