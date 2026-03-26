from openai import OpenAI
import os

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


def route_with_llm(user_message: str, context: dict):
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": """
                    You are the brain of otto, a personal assistant.

                    Your job:
                    1. Understand the user's intent
                    2. Detect follow-up questions using context
                    3. Return structured JSON ONLY

                    Possible intents:
                    - calendar_query → user wants to know what they have scheduled (today, tomorrow, etc.)
                    - calendar_followup → user refers to a specific event already mentioned
                    - expense → user is logging or talking about money spent
                    - unknown → anything else

                    Calendar understanding rules:

                    1. General queries:
                    - If the user asks about their day (e.g. "¿Qué tengo hoy?", "Do I have meetings today?") → calendar_query

                    2. Follow-ups (context-aware):
                    - If the user refers to a specific event from a previous list → calendar_followup
                    - Always return the correct index when possible

                    3. Positional references:
                    - "primero" / "first" → index 0
                    - "segundo" / "second" → index 1
                    - "tercero" / "third" → index 2
                    - "último" / "last" → last index in the list

                    4. Flexible references (IMPORTANT):
                    - "el de las 8", "the one at 8", "la reunión de las 3" → match by time if possible
                    - "esa reunión", "that one", "la última que dijiste" → infer using context

                    5. Listing behavior:
                    - If the user asks to see all events:
                    Examples:
                    - "todas"
                    - "muéstrame todo"
                    - "list them"
                    - "what are all my meetings"

                    → intent = calendar_query
                    → include flag: "list_all": true

                    6. Default behavior:
                    - If unsure but related to calendar → prefer calendar_query over unknown

                    Output format (STRICT JSON):
                    {
                    "intent": "...",
                    "index": number or null,
                    "list_all": true/false
                    }

                    Rules:
                    - If user refers to a specific event (first, second, tercero, etc) → calendar_followup
                    - Always return the correct index when possible
                    - Use context if available
                    - Be precise

                    """
                },
                {
                    "role": "user",
                    "content": f"""
                        User message: {user_message}

                        Context:
                        {context}
                        """
                }
            ],
        )

        content = response.choices[0].message.content

        if not content:
            return None

        import json
        return json.loads(content)

    except Exception as e:
        print("❌ LLM routing failed:", e)
        return None