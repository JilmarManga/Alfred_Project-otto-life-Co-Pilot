"""
E2E Test: Morning Brief

Fetches your Firestore profile, triggers a real morning brief
to your WhatsApp, and prints the message for comparison.

Usage:
    python3 app/scripts/e2e_test_morning_brief.py

Requires: valid user in Firestore (run onboarding first if needed).
"""

from dotenv import load_dotenv
load_dotenv()

from app.repositories.user_repository import UserRepository
from app.services.morning_brief.morning_brief_composer import compose_morning_insights
from app.services.morning_brief.message_builder import build_morning_message
from app.services.whatsapp_sender import send_whatsapp_message

USER_PHONE = "573043775520"


def run():
    print("=" * 60)
    print("  E2E TEST: MORNING BRIEF")
    print("=" * 60)
    print()

    # --- Step 1: Verify user profile ---
    print("[1] Fetching user profile from Firestore...")
    user = UserRepository.get_user(USER_PHONE)
    if not user:
        print("  FAIL: User not found. Run onboarding first.")
        return

    language = user.get("language", "es")
    user_name = user.get("name")
    user_location = user.get("location", "Bogotá, Colombia")

    print(f"  name:     {user_name}")
    print(f"  language: {language}")
    print(f"  location: {user_location}")
    print()

    # --- Step 2: Compose insights ---
    print("[2] Composing morning insights...")
    data = compose_morning_insights(USER_PHONE, user_location=user_location, lang=language)
    print(f"  events today: {data.event_count}")
    if data.first_event:
        print(f"  first event:  {data.first_event.get('title')} at {data.first_event.get('start')}")
        if data.first_event.get("has_location"):
            print(f"  location:     {data.first_event.get('location')}")
            print(f"  leave at:     {data.first_event.get('leave_at')}")
            print(f"  traffic:      {data.first_event.get('traffic_note')}")
    weather = data.weather or {}
    print(f"  weather:      {weather.get('summary', 'N/A')}, {weather.get('temperature', 'N/A')}")
    print()

    # --- Step 3: Build message ---
    print("[3] Building message...")
    message = build_morning_message(data=data, language=language, user_name=user_name)
    print()
    print("  " + "-" * 40)
    print(f"  {message}")
    print("  " + "-" * 40)
    print()

    # --- Step 4: Send ---
    print("[4] Sending to WhatsApp...")
    send_whatsapp_message(to=USER_PHONE, message=message)
    print("  Sent!")
    print()

    # --- Checklist ---
    print("=" * 60)
    print("  VERIFY ON YOUR PHONE")
    print("=" * 60)
    print()
    print(f"  [ ] Greeting includes name: '{user_name}'")
    print(f"  [ ] Language is {'Spanish' if language == 'es' else 'English'}")
    print(f"  [ ] Weather is for: {user_location}")
    expected_label = "Clima:" if language == "es" else "Weather:"
    print(f"  [ ] Weather label is: '{expected_label}'")
    if data.event_count > 0:
        print(f"  [ ] Shows {data.event_count} event(s)")
        if data.first_event and data.first_event.get("has_location"):
            print(f"  [ ] First event has traffic/leave info")
    print()


if __name__ == "__main__":
    run()
