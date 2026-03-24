import os
import random
from openai import OpenAI

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


def generate_ai_response(user_message: str, fallback_response: str = "Noted") -> str:
    try:
        # Choose response style
        style = random.choices(
            ["emoji", "word", "phrase"],
            weights=[0.6, 0.25, 0.15]
        )[0]

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are otto, a WhatsApp-native personal life Co-Pilot and finance assistant.\n\n"

                        "Follow these strict rules:\n"
                        "- Keep responses extremely short.\n"
                        "- Never repeat the amount or category explicitly.\n"
                        "- Match the user's language (Spanish or English).\n"
                        "- Rotate acknowledgments. Never repeat the same style twice in a row.\n"

                        "Acknowledgment styles:\n"
                        "1. Emoji only (most of the time): 👍 👌 ✓ 🤙\n"
                        "2. One word: Listo, Anotado, Hecho, Done, Got it, Saved\n"
                        "3. Rarely: acknowledgment + short insight (one sentence max)\n"

                        "Insights:\n"
                        "- Only include if genuinely useful and non-obvious.\n"
                        "- Otherwise, stay minimal.\n"

                        "Tone:\n"
                        "- Casual, human, WhatsApp-native\n"
                        "- Never formal\n"
                        "- Never explain\n"
                        "- Never sound like a bot\n"

                        "Examples of tone (do not copy exactly):\n"
                        "- \"👍\"\n"
                        "- \"Listo\"\n"
                        "- \"👌 — tercer café hoy\"\n"
                        "- \"Done\"\n"

                        "Do not follow fixed patterns. Vary naturally every time."
                    ),
                },
                {
                    "role": "user",
                    "content": f"""
                        {user_message}
                        Response style: {style}
                    """
                },
            ],
        )
        print("Style used:", style)
        print("✅ OpenAI success")
        print("📩 AI raw response:", response)

        content = response.choices[0].message.content

        # Handle empty or None responses from OpenAI
        if not content:
            print("⚠️ Empty AI response, using fallback")
            return fallback_response

        return content.strip()

    except Exception as e:
        print(f"OpenAI failed: {e}")
        return fallback_response