from app.models.inbound_message import InboundMessage
from app.repositories.user_repository import UserRepository
from app.services.whatsapp_sender import send_whatsapp_message


def handle_onboarding(inbound: InboundMessage, user: dict | None) -> bool:
    """
    Handles all onboarding states. Returns True if the message was consumed
    by onboarding (caller should return immediately). Returns False if the
    user is fully onboarded and normal processing should continue.
    """
    phone = inbound.user_phone_number
    text = (inbound.text or "").strip()

    # --- State 1: New user — no record in Firestore ---
    if not user:
        send_whatsapp_message(phone, "🐙 Hello")
        send_whatsapp_message(phone, "Español or English?")
        UserRepository.create_or_update_user(
            user_phone_number=phone,
            data={"language": None, "onboarding_completed": False},
        )
        return True

    # --- State 2: User exists but language not set ---
    if not user.get("language"):
        text_lower = text.lower()
        if "es" in text_lower or "español" in text_lower:
            language = "es"
        elif "en" in text_lower or "english" in text_lower:
            language = "en"
        else:
            send_whatsapp_message(phone, "Please reply: Español or English")
            return True

        UserRepository.create_or_update_user(
            user_phone_number=phone,
            data={"language": language},
        )

        if language == "es":
            msg = (
                "Hola, soy Otto 🐙\n\n"
                "Para empezar, cuéntame esto:\n"
                "1. ¿Cómo te llamas?\n"
                "2. ¿Qué moneda usas normalmente? (COP, USD, NZD, etc.)\n"
                "3. ¿En qué ciudad/país estás?\n\n"
                "Respóndeme con todo en un solo mensaje, ej: Otto, USD, New York 😊"
            )
        else:
            msg = (
                "Hey, I'm Otto 🐙\n\n"
                "To get started, tell me:\n"
                "1. What's your name?\n"
                "2. What currency do you use? (COP, USD, NZD, etc.)\n"
                "3. What city/country are you in?\n\n"
                "Reply with everything in one message, e.g: Otto, USD, New York 😊"
            )
        send_whatsapp_message(phone, msg)
        return True

    # --- State 3: Language set but onboarding not completed ---
    if not user.get("onboarding_completed"):
        parts = [p.strip() for p in text.split(",")]
        lang = user.get("language", "es")

        if len(parts) >= 3:
            name, currency, location = parts[0], parts[1].upper(), parts[2]
            UserRepository.create_or_update_user(
                user_phone_number=phone,
                data={
                    "name": name,
                    "preferred_currency": currency,
                    "location": location,
                    "language": lang,
                    "timezone": "America/Bogota",
                    "onboarding_completed": True,
                },
            )
            msg = f"Perfecto {name} 🙌 Ya estamos listos." if lang == "es" else f"Perfect {name} 🙌 All set."
            send_whatsapp_message(phone, msg)
        else:
            if lang == "es":
                retry = "Casi 🙌\n\nEnvíamelo en este formato:\nNombre, Moneda, Ciudad\n\nEjemplo:\nOtto, USD, New York 😊"
            else:
                retry = "Almost 🙌\n\nSend it in this format:\nName, Currency, City\n\nExample:\nOtto, USD, New York 😊"
            send_whatsapp_message(phone, retry)
        return True

    # --- State 4: Fully onboarded — let normal flow handle it ---
    return False
