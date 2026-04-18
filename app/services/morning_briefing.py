from app.services.morning_brief.morning_brief_composer import compose_morning_insights
from app.services.morning_brief.message_builder import build_morning_message
from app.services.whatsapp_sender import send_whatsapp_message


def run_morning_briefing(user: dict) -> None:
    """
    Compose and send the morning brief for a single user.
    Caller must pass the full user dict with `_refresh_token` already decrypted
    and `phone` set to the user's phone number.
    Raises on calendar/WhatsApp errors so the cron caller can log and skip.
    """
    phone = user["phone"]
    language = (user.get("language") or "es").lower()
    user_name = user.get("name")

    data = compose_morning_insights(user)
    message = build_morning_message(data=data, language=language, user_name=user_name)
    send_whatsapp_message(to=phone, message=message)
