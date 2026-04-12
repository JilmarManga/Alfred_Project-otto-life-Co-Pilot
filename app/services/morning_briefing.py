from app.services.google_calendar import get_today_events, normalize_events, summarize_day
from app.services.morning_brief.morning_brief_composer import compose_morning_insights
from app.services.morning_brief.message_builder import build_morning_message
from app.services.whatsapp_sender import send_whatsapp_message
from app.repositories.user_repository import UserRepository


def run_morning_briefing(user_phone_number: str):
    # 0. Fetch user profile from Firestore
    user = UserRepository.get_user(user_phone_number) or {}
    language = user.get("language", "es")
    user_name = user.get("name")
    user_location = user.get("location", "Bogotá, Colombia")

    # 1. Compose structured insights
    data = compose_morning_insights(user_phone_number, user_location=user_location)

    # 2. Build deterministic message
    message = build_morning_message(
        data=data,
        language=language,
        user_name=user_name
    )

    # 3. Send message
    send_whatsapp_message(
        to=user_phone_number,
        message=message
    )

    print("Morning briefing sent:", message)