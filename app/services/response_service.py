import random

EMOJI_ACKS = ["👍", "👌", "✓", "🤙"]
WORD_ACKS_EN = ["Done", "Got it", "Saved", "Noted", "Cool"]
WORD_ACKS_ES = ["Listo", "Anotado", "Hecho", "Va", "Dale", "Cool"]
MIXED_ACKS = ["Ok ✓", "Listo ✓"]


def detect_language(text: str) -> str:
    text = text.lower()
    spanish_keywords = ["gasto", "gasté", "dolares", "comida", "café"]

    for word in spanish_keywords:
        if word in text:
            return "es"
    return "en"


def pick_ack(language: str) -> str:
    rand = random.random()

    if rand < 0.6:
        return random.choice(EMOJI_ACKS)
    elif rand < 0.9:
        if language == "es":
            return random.choice(WORD_ACKS_ES)
        return random.choice(WORD_ACKS_EN)
    else:
        return random.choice(MIXED_ACKS)


def generate_response(user_text: str, expense: dict, user_stats: dict | None = None) -> str:
    language = detect_language(user_text)
    ack = pick_ack(language)

    # Days 1–3 → no insights
    return ack