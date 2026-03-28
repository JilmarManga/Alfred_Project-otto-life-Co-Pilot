from app.services.google_calendar import get_today_events, normalize_events, summarize_day
from app.services.morning_brief.morning_brief_composer import compose_morning_insights
from app.services.morning_brief.message_builder import build_morning_message
from app.services.whatsapp_sender import send_whatsapp_message


def run_morning_briefing(user_phone_number: str):
    # 1. Compose structured insights
    data = compose_morning_insights(user_phone_number)

    # 2. Build deterministic message (default Spanish for now)
    message = build_morning_message(
        data=data,
        language="es",
        user_name=None  # we add this later
    )

    # 3. Send message
    send_whatsapp_message(
        to=user_phone_number,
        message=message
    )

    print("Morning briefing sent:", message)