
# Alfred (Otto) Project 

> This file is the source of truth for any AI assistant (Claude Code or otherwise) working on this project.
> Read this BEFORE touching any code. Every architectural decision here is intentional.

---

## 🧠 What Is This Project?

**Alfred (Otto)** is a WhatsApp-based AI personal assistant. Users text Otto in natural language — no commands, no menus — and Otto handles their life: tracking expenses, managing their calendar, checking weather, and eventually much more.

**This is NOT a chatbot.** It is a behavior-driven assistant system.

Current focus: **natural-language expense tracking** as the core MVP feature.

# Alfred project is a deterministic system with probabilistic understanding and human-like communication.

---

## 🎯 Product Philosophy (NON-NEGOTIABLE)

These principles override any "cleaner" technical solution that violates them:

1. **Natural language first.** Users text like they're texting a friend. "Pague dos millones en arriendo" must work. "spent 20 on lunch" must work. Never require structured input.

2. **The LLM is a parser, not an orchestrator.** The LLM extracts meaning from messages. It does NOT decide what action to take. The deterministic router decides.

3. **Never fully rely on the LLM.** Every LLM output must be validated or normalized by deterministic logic before it triggers any action.

4. **WhatsApp-first UX.** Responses must be short, warm, emoji-friendly, and feel human. Never return JSON, error codes, or technical language to the user.

5. **Currency logic is sacred.** Default currency comes from onboarding. Only override if the user explicitly states a different currency in the message. Never assume.

6. **Firestore only.** No relational DB. No SQL. All persistence goes through Firestore.

---

## ⚙️ Tech Stack

| Layer | Technology |
|---|---|
| Language | Python 3.13 |
| Framework | FastAPI |
| Server | Uvicorn |
| Database | Firestore (Firebase) |
| Messaging | WhatsApp Cloud API (webhook) |
| AI | OpenAI GPT (via extract_expense + generate_ai_response) |
| Deployment | Render (confirm env vars before deploy) |
| Auth | Google OAuth (currently broken — see bugs) |

---

## 🏗️ Architecture: The New Model

> ⚠️ This is the TARGET architecture as of the refactor agreed with the CTO.
> The old architecture had the LLM acting as orchestrator. That is being replaced.

### The 4-Layer Model

```
User message (WhatsApp)
        ↓
┌─────────────────────────────────┐
│  LAYER 1 — PARSER (LLM)        │
│  Job: extract structured JSON   │
│  from natural language ONLY.    │
│  Never decides what to do.      │
└────────────────┬────────────────┘
                 ↓
         Structured JSON output:
         {
           "amount": 2000000,
           "currency": "COP",
           "category_hint": "arriendo",
           "date_hint": null,
           "raw_message": "pague dos millones en arriendo",
           "signals": ["gaste", "pague", "compré"]
         }
                 ↓
┌─────────────────────────────────┐
│  LAYER 2 — ROUTER (Deterministic) │
│  Job: read the JSON and decide  │
│  which agent handles this.      │
│  100% code. No LLM involved.    │
│  If ambiguous → ask user.       │
└────────────────┬────────────────┘
                 ↓
    ┌────────────┬────────────┬────────────┐
    ↓            ↓            ↓            ↓
[Expense     [Calendar    [Weather    [Unknown →
 Agent]       Agent]       Agent]      clarify]
    ↓            ↓            ↓
┌─────────────────────────────────┐
│  LAYER 3 — AGENTS (Specialized) │
│  Each agent is self-contained.  │
│  Has its own logic + DB calls.  │
│  Writes result to Firestore.    │
└────────────────┬────────────────┘
                 ↓
┌─────────────────────────────────┐
│  LAYER 4 — RESPONDER (LLM)     │
│  Job: format result as warm,    │
│  natural, human-friendly text.  │
│  Uses user's language setting.  │
└─────────────────────────────────┘
```

### Routing Rules (Deterministic)

The router reads the extracted JSON and applies these rules in order:

```python
if extracted.amount is not None:
    → ExpenseAgent

elif extracted.signals contains ["calendario", "agenda", "reunión", "meeting", "event"]:
    → CalendarAgent

elif extracted.signals contains ["clima", "weather", "lluvia", "temperatura"]:
    → WeatherAgent

elif extracted.signals contains ["resumen", "summary", "cuánto", "gasté", "spent"]:
    → SummaryAgent

else:
    → AmbiguityHandler → ask one clarifying question
```

**No confidence scores.** No thresholds. The router decides, or it asks.

---

## 🔷 Full Request Flow

```
WhatsApp message arrives
        ↓
/webhook (FastAPI)
        ↓
route_incoming_message()
        ↓
map_incoming_event_to_inbound_message()
        ↓
User lookup in Firestore
        ↓
ONBOARDING CHECK (if not completed → onboarding flow)
        ↓
Context retrieval (user_context_store)
        ↓
LAYER 1: parse_message_with_llm()
  → Returns structured JSON always
  → Converts word-numbers to digits ("dos millones" → 2000000)
  → Never returns an intent classification
        ↓
LAYER 2: deterministic_router()
  → Reads JSON fields
  → Selects agent
  → If ambiguous: returns clarification question
        ↓
LAYER 3: agent.execute()
  → ExpenseAgent / CalendarAgent / WeatherAgent / SummaryAgent
        ↓
LAYER 4: responder.format()
  → LLM formats response in user's language
  → Short, warm, emoji-friendly
        ↓
send_whatsapp_message()
```

---

## 🧱 Module Map

### Current Files

| File | Responsibility | Status |
|---|---|---|
| `whatsapp_webhook.py` | Core orchestrator, webhook ingestion, routing, onboarding | ⚠️ God object — refactor target |
| `expense_extractor.py` | LLM-based expense extraction + fallback normalization | 🔄 Becomes Layer 1 parser |
| `intent_classifier.py` | Fallback intent classifier (non-LLM) | 🔄 Absorbed into Layer 2 router |
| `llm_intent_router.py` | LLM intent detection | ❌ Deprecated by new architecture |
| `user_context_store.py` | Stores last intent, last event, today's events | ✅ Keep as-is |
| `ExpenseRepository` | Firestore expense CRUD + date-range queries | ✅ Keep as-is |
| `UserRepository` | User management + onboarding persistence | ✅ Keep as-is |

### Target File Structure (Post-Refactor)

```
alfred/
├── webhook/
│   └── whatsapp_webhook.py        # Slim — ingestion + routing only
│
├── parser/
│   └── message_parser.py          # Layer 1: LLM extraction → structured JSON
│
├── router/
│   └── deterministic_router.py    # Layer 2: pure logic, no LLM
│
├── agents/
│   ├── expense_agent.py           # Layer 3: handles expense flow end-to-end
│   ├── calendar_agent.py          # Layer 3: Google Calendar integration
│   ├── weather_agent.py           # Layer 3: weather queries
│   └── summary_agent.py           # Layer 3: expense summaries
│
├── responder/
│   └── response_formatter.py      # Layer 4: LLM formats human response
│
├── repositories/
│   ├── expense_repository.py
│   └── user_repository.py
│
├── context/
│   └── user_context_store.py
│
└── services/
    ├── google_calendar_service.py
    └── maps_service.py
```

---

## 📦 Data Models

### Firestore: `users` collection

```json
{
  "name": "Jilmar",
  "preferred_currency": "COP",
  "location": "Bogotá Colombia",
  "language": "es",
  "timezone": "America/Bogota",
  "onboarding_completed": true,
  "created_at": "timestamp",
  "updated_at": "timestamp"
}
```

### Firestore: `expenses` collection

```json
{
  "amount": 2000000,
  "currency": "COP",
  "category": "housing",
  "confidence": 0.9,
  "created_at": "timestamp",
  "user_phone_number": "573043775520",
  "user_message": "Pague dos millones en arriendo",
  "source": "whatsapp user's chat"
}
```

> ⚠️ `confidence` field is kept for historical data but is no longer used for routing decisions.

---

## 🔌 Layer 1 Parser — LLM Prompt Contract

The parser prompt must ALWAYS produce this exact JSON structure. No prose. No extra keys.

```
You are a data extractor for a personal finance assistant.

Given a user message in any language (primarily Spanish and English),
extract ONLY a structured JSON object. Do not classify intent.
Do not decide what to do. Only extract.

Rules:
- Convert ALL number words to digits: "dos" → 2, "dos millones" → 2000000,
  "two hundred" → 200, "veinte mil" → 20000
- If no amount is present, set amount to null
- Extract category_hint from context clues (e.g. "arriendo" → "housing")
- signals: list of intent-related words found in the message
- Return ONLY valid JSON. No preamble. No explanation.

Output format:
{
  "amount": <number or null>,
  "currency": <"COP"|"USD"|"EUR"|null>,
  "category_hint": <string or null>,
  "date_hint": <string or null>,
  "raw_message": <original message>,
  "signals": [<list of intent keywords found>]
}
```

---

## 🌍 Supported Features

### ✅ Working

- **Onboarding** — language, name, currency, location, timezone → Firestore
- **Expense tracking** — natural language, numbers, word-numbers, multi-currency
- **Smart categorization** — "cocacola" → food, "uber" → transport
- **Expense summary** — last 7d, 15d, current month, current year, multi-currency output
- **Travel assistant** — travel time + when to leave (Maps API)

### ⚠️ Partially Working

- **Calendar integration** — query + follow-up works; broken due to expired OAuth token

### ❌ Broken

- **Google Calendar auth** — `invalid_grant: Token has been expired or revoked`

---

## 🐛 Known Bugs (Prioritized)

### 🔴 Critical

| # | Bug | Impact |
|---|---|---|
| 1 | Google Calendar OAuth token expired | Calendar features fully unusable |
| 2 | LLM misclassifying word-number messages ("dos millones" → unknown) | Expenses not registered, user gets no response |

### 🟠 Medium

| # | Bug | Impact |
|---|---|---|
| 3 | `whatsapp_webhook.py` god object | Hard to maintain, impossible to test |
| 4 | Language mixing in responses (ES + EN) | Poor UX, partially fixed |
| 5 | Onboarding language not fully respected | EN users still get ES prompts |

### 🟡 Low

| # | Bug | Impact |
|---|---|---|
| 6 | No unit tests | Regressions go undetected |
| 7 | No CI/CD pipeline | Manual deploys, no safety net |

---

## 🔐 Environment Variables

```bash
# WhatsApp
WHATSAPP_TOKEN=
WHATSAPP_PHONE_NUMBER_ID=
WHATSAPP_VERIFY_TOKEN=

# Firebase / Firestore
FIREBASE_PROJECT_ID=
FIREBASE_PRIVATE_KEY=
FIREBASE_CLIENT_EMAIL=

# OpenAI
OPENAI_API_KEY=

# Google Calendar
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
GOOGLE_REFRESH_TOKEN=          # ⚠️ THIS IS EXPIRED — needs re-auth

# Google Maps
GOOGLE_MAPS_API_KEY=

# App
ENVIRONMENT=development|production
```

---

## 🧪 Testing Strategy (Target)

We have zero tests today. When adding tests, follow this priority:

1. **Parser tests** — unit test the LLM prompt with 20+ edge cases (word numbers, mixed language, ambiguous messages)
2. **Router tests** — unit test routing logic for every intent signal combination
3. **Agent tests** — unit test each agent with mocked Firestore
4. **Integration tests** — full flow from raw WhatsApp message to Firestore write

---

## 🚀 Deployment

- **Platform:** Render
- **Local dev:** Uvicorn (`uvicorn main:app --reload`)
- **Webhook URL must be registered** in Meta WhatsApp Cloud API dashboard
- After any change to `.env`, redeploy on Render — env vars are not hot-reloaded

---

## 🔮 Vision & Future Agents

Otto is designed to eventually support:

- Budget monitoring & alerts
- Recurring expense payment reminders
- Birthday message automation
- Morning brief (✅ already built)
- News digest by preference
- Traffic + weather pre-meeting alerts
- Homework help & document generation
- Appointment scheduling

**Architecture principle for future agents:** Every new capability = a new Agent file in `/agents/`. The router, parser, and responder layers never change. New agents plug in without touching existing code.

---

## ⚠️ What NOT To Do (Hard Rules)

1. **Never let the LLM decide the routing.** Classification belongs to the deterministic router.
2. **Never add commands or structured syntax for users.** Natural language only.
3. **Never change the Firestore schema** without updating this doc and both repositories.
4. **Never store sensitive data** (tokens, keys) in code. Always `.env`.
5. **Never make `whatsapp_webhook.py` larger.** It should get smaller with every PR.
6. **Never override user currency** unless the message explicitly states a different one.
7. **Never return technical errors to the user.** All errors → graceful human-friendly fallback message.

---

*Last updated: April 2026 — post architecture review with CTO*
*Next session: begin Layer 1 parser refactor + deterministic router implementation*
