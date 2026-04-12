"""
E2E Test: Onboarding Flow

Resets your Firestore user record, then guides you step-by-step
through testing the onboarding flow via WhatsApp.

Usage:
    python3 app/scripts/e2e_test_onboarding.py

Requires: server running (locally or on Render) to receive your WhatsApp messages.
"""

from dotenv import load_dotenv
load_dotenv()

from app.core.firebase import db
from app.repositories.user_repository import UserRepository

USER_PHONE = "573043775520"


def delete_user(phone: str):
    db.collection("users").document(phone).delete()


def print_user_state(phone: str):
    user = UserRepository.get_user(phone)
    if not user:
        print("  (no record in Firestore)")
    else:
        for key in ["name", "language", "preferred_currency", "location", "onboarding_completed"]:
            print(f"  {key}: {user.get(key)}")


def run():
    print("=" * 60)
    print("  E2E TEST: ONBOARDING FLOW")
    print("=" * 60)
    print()

    # --- Reset ---
    print("[RESET] Deleting user from Firestore...")
    delete_user(USER_PHONE)
    print("[RESET] Done. User state:")
    print_user_state(USER_PHONE)
    print()

    # --- Step 1 ---
    print("-" * 60)
    print("STEP 1: New user greeting")
    print("-" * 60)
    print("  ACTION:  Send any message to Otto on WhatsApp (e.g. 'hola')")
    print("  EXPECT:  Two messages:")
    print("           1. '🐙 Hello'")
    print("           2. 'Español or English?'")
    print()
    input("  Press Enter after you've sent the message and received the reply...")
    print()
    print("  Firestore state after Step 1:")
    print_user_state(USER_PHONE)
    print()

    # --- Step 2 ---
    print("-" * 60)
    print("STEP 2: Regression test — 'yes' must NOT match Spanish")
    print("-" * 60)
    print("  ACTION:  Send 'yes' on WhatsApp")
    print("  EXPECT:  Retry prompt: 'Please reply: Español or English'")
    print("           (Before the fix, 'yes' was detected as Spanish)")
    print()
    input("  Press Enter after you've verified the retry prompt...")
    print()
    print("  Firestore state after Step 2 (language should still be None):")
    print_user_state(USER_PHONE)
    print()

    # --- Step 3 ---
    print("-" * 60)
    print("STEP 3: Language selection — Spanish")
    print("-" * 60)
    print("  ACTION:  Send 'español' on WhatsApp")
    print("  EXPECT:  Spanish profile setup message:")
    print("           'Hola, soy Otto 🐙 ...'")
    print("           Asking for name, currency, and city")
    print()
    input("  Press Enter after you've received the profile questions...")
    print()
    print("  Firestore state after Step 3 (language should be 'es'):")
    print_user_state(USER_PHONE)
    print()

    # --- Step 4 ---
    print("-" * 60)
    print("STEP 4: Complete profile")
    print("-" * 60)
    print("  ACTION:  Send 'Jilmar, COP, Bogotá, Colombia' on WhatsApp")
    print("  EXPECT:  Confirmation: 'Perfecto Jilmar 🙌 Ya estamos listos.'")
    print()
    input("  Press Enter after you've received the confirmation...")
    print()
    print("  Firestore state after Step 4 (should be fully onboarded):")
    print_user_state(USER_PHONE)
    print()

    # --- Summary ---
    print("=" * 60)
    print("  RESULTS")
    print("=" * 60)
    user = UserRepository.get_user(USER_PHONE)
    if not user:
        print("  FAIL: User not found in Firestore after onboarding")
        return

    checks = [
        ("name", "Jilmar", user.get("name")),
        ("language", "es", user.get("language")),
        ("preferred_currency", "COP", user.get("preferred_currency")),
        ("location", "Bogotá, Colombia", user.get("location")),
        ("onboarding_completed", True, user.get("onboarding_completed")),
    ]

    all_passed = True
    for field, expected, actual in checks:
        status = "PASS" if actual == expected else "FAIL"
        if status == "FAIL":
            all_passed = False
        print(f"  [{status}] {field}: expected={expected}, actual={actual}")

    print()
    if all_passed:
        print("  All checks passed!")
    else:
        print("  Some checks failed. Review the output above.")
    print()


if __name__ == "__main__":
    run()
