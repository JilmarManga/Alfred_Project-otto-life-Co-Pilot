from app.services.google_calendar import (
    get_today_events,
    normalize_events,
    summarize_day
)
from app.services.whatsapp_sender import send_whatsapp_message


def run_morning_briefing(user_phone_number: str):
    # 1. Get events
    events = get_today_events()
    events = normalize_events(events)

    # 2. Generate summary
    summary = summarize_day(events)

    # 3. Send to user
    send_whatsapp_message(
        to=user_phone_number,
        message=summary
    )

    print("Morning briefing sent:", summary)