# CLAUDE.md — Alfred (Otto)

WhatsApp-based AI personal assistant. Users text naturally ("Pague dos millones en arriendo") — Otto tracks expenses, calendars, weather, travel, and more. **Not a chatbot. Deterministic system with probabilistic understanding.**

---

## Product Philosophy (NON-NEGOTIABLE)

1. **Natural language only.** Never require commands, structured input, or menus.
2. **LLM is a parser, not an orchestrator.** LLM extracts data. A deterministic router decides actions.
3. **Validate all LLM output** with deterministic logic before triggering any action.
4. **WhatsApp-first UX.** Short, warm, emoji-friendly. Never return JSON, errors, or technical language to users.
5. **Currency is sacred.** Default = `preferred_currency` from Firestore. Only override if user explicitly states another.
6. **Firestore only.** No SQL. All persistence through Firestore.

---

## Tech Stack

Python 3.13 · FastAPI · Uvicorn · Firestore · WhatsApp Cloud API · OpenAI GPT-4o-mini · OpenWeatherMap · Google Maps Directions API · Google Calendar API (OAuth via token.json) · Render

---

## 4-Layer Architecture (BUILT — production ready)

### Request Flow
```
WhatsApp POST /webhook
  -> route_incoming_message()                [services/message_router.py]
  -> map_incoming_event_to_inbound_message() [services/inbound_message_mapper.py]
  -> UserRepository.get_user()               [repositories/user_repository.py]
  -> handle_onboarding()                     [handlers/onboarding_handler.py]  ← gate, not an agent
  -> parse_message()          LAYER 1        [parser/message_parser.py]
  -> route()                  LAYER 2        [router/deterministic_router.py]
  -> agent.execute()          LAYER 3        [agents/*.py]
  -> format_response()        LAYER 4        [responder/response_formatter.py]
  -> send_whatsapp_message()                 [services/whatsapp_sender.py]
```

### Layer 1 — Parser (`app/parser/message_parser.py`)
**Responsibility:** Convert raw natural language → `ParsedMessage`. Nothing else.

**Output model** (`app/models/parsed_message.py`):
```python
ParsedMessage(
    amount: Optional[float],
    currency: Optional[str],       # "COP" | "USD" | "EUR" | None
    category_hint: Optional[str],
    date_hint: Optional[str],
    raw_message: str,
    signals: List[str],            # ALWAYS deterministic keyword scan, never from LLM
    event_reference: Optional[EventReference],
)
```

**Rules:**
- LLM extracts `amount`, `currency`, `category_hint`, `date_hint` — nothing else
- `signals` are populated by deterministic keyword scan (`_scan_signals`) — never from LLM output
- `parse_word_numbers()` runs as fallback if LLM returns null for amount (handles "dos millones", "50 mil", "200 mil pesos")
- `word_number_parser.py` handles digit+multiplier combos: "50 mil" → 50000, "2 millones" → 2000000
- Full heuristic fallback (regex + word_number_parser) if LLM call fails entirely

**Forbidden:** classifying intent, deciding actions, calling Firestore, returning anything beyond the model fields

---

### Layer 2 — Router (`app/router/deterministic_router.py`)
**Responsibility:** Read `ParsedMessage` → return correct agent instance. Pure logic, no LLM.

**Routing priority order (strict — do not reorder without good reason):**
```python
if parsed.amount is not None:    -> ExpenseAgent
if signal in TRAVEL_KEYWORDS:    -> TravelAgent      # checked before calendar — avoids "reunion" collision
if signal in WEATHER_KEYWORDS:   -> WeatherAgent
if signal in CALENDAR_KEYWORDS:  -> CalendarAgent
if signal in SUMMARY_KEYWORDS:   -> SummaryAgent
else:                            -> AmbiguityAgent
```

**Keyword sets:**
```python
CALENDAR_KEYWORDS = {"calendario", "agenda", "reunion", "reunión", "meeting", "event", "evento", "tengo"}
WEATHER_KEYWORDS  = {"clima", "weather", "lluvia", "temperatura", "temperature", "rain", "calor", "frio"}
SUMMARY_KEYWORDS  = {"resumen", "summary", "cuanto", "cuánto", "gaste", "gasté", "spent", "gastos", "expenses"}
TRAVEL_KEYWORDS   = {"llegar", "llego", "tiempo", "tráfico", "trafico", "traffic", "travel", "arrive", "salir", "leave"}
```

**Important design decisions:**
- "hoy", "today", "mañana", "tomorrow" are intentionally NOT in `CALENDAR_KEYWORDS` — they are time modifiers, not intent signals. "tengo" is the calendar-intent word ("qué tengo hoy?")
- Travel is checked before Calendar so "a qué hora debo salir para mi reunión?" routes to TravelAgent, not CalendarAgent
- These same keyword sets are mirrored in `parser/message_parser.py` for signal scanning

**Forbidden:** calling LLM, calling Firestore, confidence scores, making routing assumptions

---

### Layer 3 — Agents (`app/agents/`)
**Responsibility:** Execute business logic for one domain. Own their Firestore reads/writes.

**Output model** (`app/models/agent_result.py`):
```python
AgentResult(agent_name: str, success: bool, data: dict, error_message: Optional[str])
```

| Agent | File | Responsibility |
|---|---|---|
| `ExpenseAgent` | `expense_agent.py` | Validate amount, normalize currency, save to Firestore `expenses` |
| `CalendarAgent` | `calendar_agent.py` | Fetch today's events, handle follow-up queries via context |
| `TravelAgent` | `travel_agent.py` | Find next event, call Maps API for leave time and duration |
| `SummaryAgent` | `summary_agent.py` | Query expenses by date range, aggregate by currency |
| `WeatherAgent` | `weather_agent.py` | Fetch weather; extracts city from message if user specifies one |
| `AmbiguityAgent` | `ambiguity_agent.py` | Pass raw message to responder to generate a clarifying question |

**Key behaviors:**
- `ExpenseAgent`: category must be validated against `{"food", "transport", "shopping", "health", "other"}` before building `ExtractedExpense` (Pydantic Literal). Non-standard hints (e.g. "housing", "rent") fall back to "other" then keyword scan.
- `WeatherAgent`: if user says "clima en Bogota", extracts "Bogota" and uses it instead of stored location. Returns `city_not_found: True` in data when OpenWeatherMap returns 404 — responder formats a helpful retry message.
- `SummaryAgent`: handles "hoy", "esta semana", "semana pasada", "este mes", "mes pasado", "este año". Default is current week (Monday–now).
- `CalendarAgent` + `TravelAgent`: use in-memory `user_context_store` for short-lived conversational context (today's events, last referenced event for "y el segundo?" follow-ups). This is intentional — context is ephemeral.

**Forbidden:** calling LLM, formatting user-facing text, knowing about WhatsApp

---

### Layer 4 — Responder (`app/responder/response_formatter.py`)
**Responsibility:** Convert `AgentResult` → warm WhatsApp message in the user's language.

**Two response paths:**
- `result.success=False` → returns from `_ERROR_MESSAGES` dict (no LLM call). Error message is distinct from success fallback so failures are visible to the user.
- `result.success=True` → calls GPT-4o-mini with `FORMATTING_PROMPT`; falls back to `_FALLBACKS` dict if LLM fails.

**Language enforcement:** Prompt says "You MUST respond in {lang_name} ONLY" and user_content repeats it. Language comes from `user["language"]` (Firestore).

**Agent-specific prompt rules:**
- `ExpenseAgent`: max 1 emoji or 1 word. Never repeat amount/category/currency.
- `SummaryAgent`: per-currency lines with thousands separators.
- `WeatherAgent`: 1 line with temp + description + emoji. If `city_not_found` in data, guide user to use full city name.
- `AmbiguityAgent`: warm greeting + one clarifying question. Never just an emoji.

**Important:** `FORMATTING_PROMPT.format(lang_name=...)` is inside the try/except block. Never put format strings with `{variable}` in the prompt text — use `[variable]` for examples instead.

**Forbidden:** making routing decisions, calling Firestore or external APIs, returning technical content

---

## Onboarding Flow (`app/handlers/onboarding_handler.py`)

Runs as a gate BEFORE the 4-layer pipeline. Returns `True` (consumed) or `False` (proceed to pipeline).

**States:**
1. No Firestore record → greeting + language question → create user doc
2. `language` not set → detect "español"/"english" → save language → ask for name/currency/city
3. `onboarding_completed=False` → parse "Name, Currency, City" format → save profile → complete
4. `onboarding_completed=True` → return False → normal pipeline runs

Onboarding is NOT an agent. It has no `ParsedMessage` — it runs before the parser.

---

## Firestore Collections

**`users`** (doc ID = phone number e.g. `+573001234567`):
```
name, preferred_currency, location, language, timezone,
onboarding_completed, created_at, updated_at
```
- `location`: full city name for Maps/Weather APIs (e.g. "Bogotá, Colombia", "San Francisco, CA"). Abbreviations like "SF" will fail with OpenWeatherMap.
- `language`: "es" | "en" — controls all response language

**`expenses`** (auto ID):
```
user_phone_number, amount, currency, category, confidence,
user_message, source, created_at
```
- `category`: one of `"food"`, `"transport"`, `"shopping"`, `"health"`, `"other"`
- `source`: always `"whatsapp user's chat"`

**`user_context`** (doc ID = phone number):
- Firestore-backed context store (`app/db/firestore_context_store.py`) — exists but NOT used by agents
- Agents use in-memory `user_context_store.py` for ephemeral conversational state (resets on server restart — intentional for short-lived follow-up context)

---

## File Structure

```
app/
├── api/
│   └── whatsapp_webhook.py          # ~70 lines — ingestion + pipeline orchestration only
├── parser/
│   ├── message_parser.py            # Layer 1: LLM extraction → ParsedMessage
│   └── word_number_parser.py        # Utility: "dos millones"→2000000, "50 mil"→50000
├── router/
│   └── deterministic_router.py      # Layer 2: pure keyword routing, no LLM
├── agents/
│   ├── base_agent.py                # Abstract base: execute(parsed, user) -> AgentResult
│   ├── expense_agent.py             # Save expenses, validate category literals
│   ├── calendar_agent.py            # Google Calendar query + follow-up
│   ├── travel_agent.py              # Maps API leave-time calculation
│   ├── summary_agent.py             # Expense aggregation by date range + currency
│   ├── weather_agent.py             # Weather lookup, city extraction from message
│   └── ambiguity_agent.py           # Pass-through for unclear messages
├── responder/
│   └── response_formatter.py        # Layer 4: LLM formats warm WhatsApp message
├── handlers/
│   └── onboarding_handler.py        # Pre-pipeline gate: new user flow
├── repositories/
│   ├── expense_repository.py        # Firestore CRUD for expenses
│   └── user_repository.py           # Firestore CRUD for users
├── db/
│   ├── firestore_context_store.py   # Persistent context (Firestore-backed, not used by agents)
│   └── user_context_store.py        # In-memory context (used by CalendarAgent, TravelAgent)
├── models/
│   ├── parsed_message.py            # Layer 1 output contract
│   ├── agent_result.py              # Layer 3 output contract
│   ├── extracted_expense.py         # Pydantic model for expense save
│   ├── inbound_message.py           # Normalized incoming WhatsApp message
│   └── webhook_event.py             # Raw webhook payload model
├── services/
│   ├── google_calendar.py           # Calendar API + OAuth auto-refresh
│   ├── maps/maps_service.py         # Google Maps Directions API
│   ├── weather/weather_service.py   # OpenWeatherMap API (language-aware)
│   ├── morning_brief/               # Morning brief composer (separate scheduled feature)
│   ├── message_router.py            # Parses raw WhatsApp payload → IncomingMessageEvent
│   ├── inbound_message_mapper.py    # IncomingMessageEvent → InboundMessage
│   └── whatsapp_sender.py           # Sends messages via WhatsApp Cloud API
├── scripts/
│   ├── reauthorize_calendar.py      # One-time OAuth reauth (run locally, opens browser)
│   └── run_morning_brief.py         # Manual trigger for morning brief
├── core/
│   └── firebase.py                  # Firestore client initialization
└── main.py                          # FastAPI app entry point

# Legacy files (still present, no longer used by main pipeline):
app/routers/llm_intent_router.py     # DEAD — LLM routing violation. Do not extend.
app/services/intent_classifier.py    # DEAD — absorbed into deterministic router.
app/services/expense_extractor.py    # DEAD — absorbed into parser + expense_agent.
app/ai/ai_service.py                 # DEAD — absorbed into responder.
app/services/response_service.py     # DEAD — absorbed into responder.
```

---

## Environment Variables

```
WHATSAPP_ACCESS_TOKEN
WHATSAPP_PHONE_NUMBER_ID
WHATSAPP_VERIFY_TOKEN

FIREBASE_CREDENTIALS_PATH          # or credentials/firebase-service-account.json

OPENAI_API_KEY

GOOGLE_MAPS_API_KEY                # Requires Directions API enabled in GCP
OPENWEATHER_API_KEY

ENVIRONMENT=development|production
```

**Google Calendar OAuth:** credentials stored in `credentials/token.json`. Auto-refreshes on expiry. If `invalid_grant` error appears, run `python3 app/scripts/reauthorize_calendar.py` locally (opens browser for consent).

---

## Hard Rules

1. **Never let the LLM decide routing.** `llm_intent_router.py` is dead. Do not extend it.
2. **Never add structured commands for users.** Natural language only.
3. **Never change Firestore schema** without updating this doc and both repositories.
4. **Never store secrets in code.** Always `.env`.
5. **Never make `whatsapp_webhook.py` larger.** Every change should shrink it or keep it at ~70 lines.
6. **Never override user currency** unless the message explicitly states one.
7. **Never return technical errors to users.** Always a graceful human-friendly fallback.
8. **New capability = new Agent in `/agents/`.** Never add feature branches inside the webhook.
9. **Never use `user_context_store.py` for durable data.** It's in-memory. Use Firestore for anything that must survive restarts.
10. **LLM only in Layer 1 (parser) and Layer 4 (responder).** Never in router or agents.
11. **Never put `{variable}` inside `FORMATTING_PROMPT` examples.** Use `[variable]` instead — Python's `.format()` will try to substitute it and crash.
12. **Travel is checked before Calendar in the router.** Do not reorder. "salir para mi reunión" must route to TravelAgent.

---

## Adding a New Agent (checklist)

1. Create `app/agents/your_agent.py` extending `BaseAgent`
2. `execute(parsed: ParsedMessage, user: dict) -> AgentResult`
3. Add keyword set to `deterministic_router.py` and `message_parser.py`
4. Add routing rule in `deterministic_router.py` (respect priority order)
5. Add `_FALLBACKS` and `_ERROR_MESSAGES` entries in `response_formatter.py`
6. Add agent-specific formatting instructions in `FORMATTING_PROMPT`
7. Never touch `whatsapp_webhook.py`
