COPY = {
    "language_prompt": """Hey 👋 / Hola 👋
What language do you prefer? ¿En qué idioma prefieres hablar?

🇬🇧 English
🇨🇴 Español""",
    "language_retry": "Please reply: English or Español 🙏",
    "beta_welcome": {
        "en": """You are one of the 10 people we've chosen to help us build Otto.

We are still building — and that is exactly why you're here. Use it as if it were yours. Break it, push it to its limits, and tell it what you wish it could do.

Respond to any message Otto sends you. We will improve what needs improving and add what is missing; everything you want it to do, we will add progressively.

Welcome to the co-pilot's seat. ☀️""",
        "es": """Eres una de las 10 personas que elegimos para darle forma a Otto.

Todavía estamos construyendo — y precisamente por eso estás aquí. Úsalo como si fuera tuyo. Rómpelo, llévalo al límite, dile qué desearías que pudiera hacer.

Responde cualquier mensaje que te mande Otto. Mejoraremos lo que se deba, agregaremos lo que haga falta, todo lo que quieras que haga lo agregaremos progresivamente.

Bienvenido al asiento del copiloto. ☀️""",
    },
    "intro": {
        "en": """Hey 👋 I'm Otto 🐙 — your personal co-pilot for the day ahead.

Every morning I'll send you a briefing: your calendar, weather, and how long it'll take to get to your first event. You can also ask me anything during the day.

What's your name and what city are you based in?""",
        "es": """Hola 👋 soy Otto 🐙 — tu copiloto personal para cada día.

Cada mañana te envío un resumen: tu calendario, el clima y cuánto tardarás en llegar a tu primer evento. También puedes preguntarme lo que quieras durante el día.

¿Cómo te llamas y en qué ciudad vives?""",
    },
    "ask_name_only": {
        "en": "Got your city ✅ — what's your first name?",
        "es": "Listo con tu ciudad ✅ — ¿cómo te llamas?",
    },
    "ask_city_only": {
        "en": "Thanks {name} 🙌 — what city are you based in?",
        "es": "Gracias {name} 🙌 — ¿en qué ciudad estás?",
    },
    "ask_profile_retry": {
        "en": "I didn't catch that — can you tell me your name and city? e.g. \"Otto, New York\"",
        "es": "No te entendí — ¿me dices tu nombre y ciudad? ej: \"Otto, Bogotá\"",
    },
    "city_not_found": {
        "en": "I couldn't find that city — can you add the country? e.g. \"Funza, Colombia\"",
        "es": "No encontré esa ciudad — ¿me agregas el país? ej: \"Funza, Colombia\"",
    },
    "city_ambiguous": {
        "en": "There are a few places called \"{city}\" — which country?",
        "es": "Hay varios lugares llamados \"{city}\" — ¿en qué país?",
    },
    "oauth_link": {
        "en": """Good to meet you, {name} 🤝

To send your morning briefing I need access to your Google Calendar — so I know what your day looks like before you do.

Connect it here 👇
{link}

Takes 30 seconds. I only read your calendar — I never modify your events.""",
        "es": """Mucho gusto, {name} 🤝

Para enviarte tu resumen matutino necesito acceso a tu Google Calendar — así sé cómo será tu día antes que tú.

Conéctalo aquí 👇
{link}

Toma 30 segundos. Solo leo tu calendario — nunca modifico tus eventos.""",
    },
    "oauth_followup": {
        "en": """{name} — whenever you're ready, here's the link to connect your calendar 👇
{link}

No calendar = no briefing, but everything else still works.""",
        "es": """{name} — cuando estés listo, aquí está el link para conectar tu calendario 👇
{link}

Sin calendario = sin resumen, pero todo lo demás sigue funcionando.""",
    },
    "oauth_pending_calendar_query": {
        "en": "I don't have access to your calendar yet — connect it here and I'll always know 👇\n{link}",
        "es": "Aún no tengo acceso a tu calendario — conéctalo aquí y siempre sabré 👇\n{link}",
    },
}


def get(key: str, lang: str = "en", **kwargs) -> str:
    entry = COPY[key]
    if isinstance(entry, dict):
        template = entry.get(lang) or entry.get("en")
    else:
        template = entry
    return template.format(**kwargs) if kwargs else template
