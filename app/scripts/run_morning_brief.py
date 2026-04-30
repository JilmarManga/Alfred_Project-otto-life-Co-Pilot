from app.repositories.user_repository import UserRepository
from app.services.morning_briefing import run_morning_briefing
from app.services.token_crypto import decrypt

USER_PHONE = "+573043775520"

if __name__ == "__main__":
    user = UserRepository.get_user(USER_PHONE)
    if not user:
        raise SystemExit(f"No user for {USER_PHONE}")
    user["phone"] = USER_PHONE
    user["_refresh_token"] = decrypt(user["google_calendar_refresh_token"])
    run_morning_briefing(user)
