import re


def resolve_city(raw_message: str, user: dict) -> str:
    """Return the city to query: explicit mention in message, else user's stored location."""
    mentioned = _extract_city_from_message(raw_message)
    if mentioned:
        return mentioned
    return user.get("location", "Bogotá, Colombia")


def _extract_city_from_message(text: str) -> str | None:
    match = re.search(
        r'\b(?:en|in)\s+([A-ZÁÉÍÓÚÑ][a-záéíóúñA-ZÁÉÍÓÚÑ]+(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñA-ZÁÉÍÓÚÑ]+)?)',
        text,
    )
    if match:
        return match.group(1).strip()
    return None
