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

Python 3.13 · FastAPI · Uvicorn · Firestore · WhatsApp Cloud API · OpenAI GPT-4o-mini · OpenWeatherMap · Google Maps Directions API · Google Calendar API (per-user OAuth, Fernet-encrypted tokens) · Railway (NIXPACKS)

---

## 4-Layer Architecture (BUILT — production ready)

### Request Flow
```
WhatsApp POST /webhook
  -> route_incoming_message()                [services/message_router.py]
  -> map_incoming_event_to_inbound_message() [services/inbound_message_mapper.py]
  -> UserRepository.get_user()               [repositories/user_repository.py]
  -> handle_onboarding()                     [handlers/onboarding_handler.py]  ← async gate, runs BEFORE pipeline
  -> handle_pending_expense()                [handlers/pending_expense_handler.py]  ← currency follow-up gate
  -> handle_pending_event()                  [handlers/pending_event_handler.py]    ← calendar-clarify follow-up gate
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
    # Event creation fields — filled only when the user describes a NEW event
    event_title: Optional[str],
    event_start: Optional[str],           # ISO 8601 w/ tz offset
    event_location: Optional[str],
    event_duration_minutes: Optional[int],
)
```

**Rules:**
- LLM extracts `amount`, `currency`, `category_hint`, `date_hint`, and (when describing a NEW event) `event_title`, `event_start`, `event_location`, `event_duration_minutes` — nothing else
- `signals` are populated by deterministic keyword scan (`_scan_signals`) — never from LLM output
- `parse_word_numbers()` runs as fallback if LLM returns null for amount (handles "dos millones", "50 mil", "200 mil pesos")
- `word_number_parser.py` handles digit+multiplier combos: "50 mil" → 50000, "2 millones" → 2000000
- Full heuristic fallback (regex + word_number_parser) if LLM call fails entirely
- `parse_message(raw_text, user_context={"today", "tz"})` — the webhook injects today's date and IANA tz (from `users/{phone}.timezone`) so the LLM can resolve "next Wednesday" / "mañana" to an absolute ISO datetime
- **Creation-intent safeguard:** if LLM returns `event_title` + `event_start`, the parser sets `amount=None` and `event_reference=None` (defeats clock-time-as-amount and "next Wednesday" → EventReference collision)
- Clock times ("2pm", "las 3", "14:00") are NEVER amounts — enforced both by prompt and safeguard above

**Forbidden:** classifying intent, deciding actions, calling Firestore, returning anything beyond the model fields

---

### Layer 2 — Router (`app/router/deterministic_router.py`)
**Responsibility:** Read `ParsedMessage` → return correct agent instance. Pure logic, no LLM.

**Routing priority order (strict — do not reorder without good reason):**
```python
if signal in REMINDER_TOGGLE_KEYWORDS: -> CalendarAgent  # settings — bypasses everything else
if parsed.amount is not None:          -> ExpenseAgent
if signal in TRAVEL_KEYWORDS:          -> TravelAgent    # checked before calendar — avoids "reunion" collision
if signal in WEATHER_KEYWORDS:         -> WeatherAgent
if signal in SUMMARY_KEYWORDS:         -> SummaryAgent   # specific money words beat generic calendar words like "have"
if signal in CALENDAR_KEYWORDS:        -> CalendarAgent
if signal in CREATE_KEYWORDS:          -> CalendarAgent  # event creation intent without a calendar noun
if parsed.event_reference is not None: -> CalendarAgent  # ordinal/next follow-ups with no keyword
if signal in GREETING_KEYWORDS:        -> GreetingAgent  # social signals after all functional agents
if signal in GRATITUDE_KEYWORDS:       -> GreetingAgent
else:                                  -> AmbiguityAgent
```

**Keyword sets:**
```python
CALENDAR_KEYWORDS  = {"calendario", "agenda", "reunion", "reunión", "meeting", "event", "evento", "tengo", "schedule", "have", "day", "busy"}
WEATHER_KEYWORDS   = {"clima", "weather", "lluvia", "temperatura", "temperature", "rain", "calor", "frio"}
SUMMARY_KEYWORDS   = {"resumen", "summary", "cuanto", "cuánto", "gaste", "gasté", "spent", "gastos", "expenses",
                       "wasted", "waste", "spend", "money", "dinero", "plata", "gastado"}
TRAVEL_KEYWORDS    = {"llegar", "llego", "tiempo", "tráfico", "trafico", "traffic", "travel", "arrive", "salir", "leave"}
GREETING_KEYWORDS  = {"hola", "hello", "hey", "buenos días", "buenos dias", "good morning", "buenas tardes",
                       "good afternoon", "buenas noches", "good evening", "buenas", "que tal", "qué tal"}
GRATITUDE_KEYWORDS = {"gracias", "thanks", "thank you", "thankss", "thanx", "grax", "tks"}
# Multi-word phrases only — matched via substring, so never include bare words
# that might collide (no "schedule" alone, no "off" alone, etc.).
CREATE_KEYWORDS    = {"agendar", "agenda una|un|el|mi", "crea/crear una|un|el|mi|la",
                       "agregar al calendario", "añade/añadir al calendario",
                       "programa(r) una|un", "nueva reunión", "nuevo evento",
                       "add event|a meeting|an event", "create event|meeting|a meeting|an event",
                       "schedule a|an|my", "book a|an|me",
                       "set up a meeting", "new meeting|event",
                       "put it on my calendar", "add to my calendar"}
REMINDER_OFF_KEYWORDS = {"recordatorios off", "desactivar/desactiva (los) recordatorios",
                          "quitar/quita recordatorios", "sin recordatorios", "apaga(r) recordatorios",
                          "turn off reminders", "stop reminders", "disable reminders",
                          "mute reminders", "no more reminders"}
REMINDER_ON_KEYWORDS  = {"recordatorios on", "activar/activa (los) recordatorios",
                          "reactivar recordatorios", "encender/enciende recordatorios",
                          "turn on reminders", "enable reminders", "start reminders",
                          "resume reminders"}
REMINDER_TOGGLE_KEYWORDS = REMINDER_OFF_KEYWORDS | REMINDER_ON_KEYWORDS
```
The exact keyword strings live in `app/parser/message_parser.py` and `app/router/deterministic_router.py` — the pseudo-grammar above ("a|b|c") is doc shorthand only.

**Important design decisions:**
- Reminder toggle is **priority #1** so "disable reminders" wins over anything else. It's a settings action with no calendar noun, so without this it would fall to AmbiguityAgent.
- "hoy", "today", "mañana", "tomorrow" are intentionally NOT in `CALENDAR_KEYWORDS` — they are time modifiers, not intent signals. "tengo" / "have" / "day" are the calendar-intent words.
- Summary is checked before Calendar: `"have"` + `"spent"` → SummaryAgent wins (specific money words beat generic calendar words).
- Travel is checked before Calendar so "a qué hora debo salir para mi reunión?" routes to TravelAgent, not CalendarAgent.
- `CREATE_KEYWORDS` entries are **multi-word phrases** ("crear una", "schedule a"), not bare verbs. A bare "schedule" would collide with "schedule my gym"/"schedule my call"-style messages that aren't creation intent. Bare verbs that are safe in isolation (e.g. "agendar") are kept.
- `REMINDER_*_KEYWORDS` entries are also multi-word phrases — never bare "stop", "off", "enable", since those substring-match benign words.
- `event_reference` routing catches ordinal follow-ups ("Y el segundo?") that contain no keyword.
- Greeting and gratitude are checked AFTER all functional agents so "hola, cuanto gaste hoy?" routes to SummaryAgent, not GreetingAgent.
- "hi" is intentionally NOT in `GREETING_KEYWORDS` — it's a substring of "this", "children", etc. Similarly "ty" is excluded from `GRATITUDE_KEYWORDS`.
- These same keyword sets are mirrored in `parser/message_parser.py` for signal scanning.

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
| `CalendarAgent` | `calendar_agent.py` | Query today's events, create events, clarify ambiguous intent, toggle reminder setting |
| `TravelAgent` | `travel_agent.py` | Find next event, call Maps API for leave time and duration |
| `SummaryAgent` | `summary_agent.py` | Query expenses by date range, aggregate by currency |
| `WeatherAgent` | `weather_agent.py` | Fetch weather; extracts city from message if user specifies one |
| `GreetingAgent` | `greeting_agent.py` | Hardcoded greeting/gratitude responses, no LLM, no Firestore |
| `AmbiguityAgent` | `ambiguity_agent.py` | Pass raw message to responder to generate a clarifying question |

**Key behaviors:**
- `ExpenseAgent`: category must be validated against `{"food", "transport", "shopping", "health", "other"}` before building `ExtractedExpense` (Pydantic Literal). Non-standard hints (e.g. "housing", "rent") fall back to "other" then keyword scan.
- `WeatherAgent`: if user says "clima en Bogota", extracts "Bogota" and uses it instead of stored location. Returns `city_not_found: True` in data when OpenWeatherMap returns 404 — responder formats a helpful retry message.
- `SummaryAgent`: handles "hoy", "esta semana", "semana pasada", "este mes", "mes pasado", "este año". Default is current week (Monday–now).
- `CalendarAgent` + `TravelAgent`: use in-memory `user_context_store` for short-lived conversational context (today's events, last referenced event for "y el segundo?" follow-ups). This is intentional — context is ephemeral.
- `CalendarAgent` branches (inside `execute`, checked in this order):
  1. `REMINDER_OFF/ON_KEYWORDS` → `_handle_reminder_toggle` (flips `calendar_reminders_enabled`, no calendar API call). Runs BEFORE `_get_refresh_token` so a disconnected user can still change the setting.
  2. CREATE keyword + event fields → `_handle_creation` (calls `create_event_for_user`, returns `data.type="calendar_create"` with `follow_up_message` dispatched as a separate WhatsApp message).
  3. CREATE keyword without fields → `error_message="missing_event_details"`.
  4. Event fields without CREATE keyword → `_handle_clarify_creation` (stashes pending event in `user_context_store`, returns `type="calendar_clarify_create"` — responder renders a deterministic yes/no question, `pending_event_handler` intercepts the reply).
  5. `event_reference` → `_handle_followup` (ordinal/next event query).
  6. Otherwise → `_handle_query` (today's events).
- `GreetingAgent`: hardcoded responses (no LLM). Picks randomly from 3-4 options per type (greeting/gratitude) per language. Greeting responses include user name. Responder short-circuits — returns `data["response"]` directly, no LLM formatting call.

**Forbidden:** calling LLM, formatting user-facing text, knowing about WhatsApp

---

### Layer 4 — Responder (`app/responder/response_formatter.py`)
**Responsibility:** Convert `AgentResult` → warm WhatsApp message in the user's language.

**Response paths (checked in order):**
- `GreetingAgent` + `result.success=True` → short-circuits with `data["response"]` (hardcoded, no LLM call).
- `type="calendar_clarify_create"` → short-circuits with a deterministic yes/no question via `_build_clarify_message()` (no LLM).
- `type="reminder_opt_out"` / `type="reminder_opt_in"` → short-circuits with hardcoded ES/EN copy (no LLM).
- `ExpenseAgent` with `needs_currency=True` → short-circuits with the currency-ask prompt.
- `result.success=False` → first tries `_SPECIFIC_ERRORS` (`missing_event_details`, `create_failed`, `reminder_toggle_failed`), then falls back to the per-agent entry in `_ERROR_MESSAGES`. No LLM call.
- `result.success=True` (all other agents) → calls GPT-4o-mini with `FORMATTING_PROMPT`; falls back to `_TYPE_FALLBACKS` (per-`data.type`) then `_FALLBACKS` (per-agent) if LLM fails. `follow_up_message` is stripped from the LLM input so it doesn't leak into the rendered reply.

**Language enforcement:** Prompt says "You MUST respond in {lang_name} ONLY" and user_content repeats it. Language comes from `user["language"]` (Firestore).

**Agent-specific prompt rules:**
- `ExpenseAgent`: max 1 emoji or 1 word. Never repeat amount/category/currency.
- `SummaryAgent`: per-currency lines with thousands separators.
- `WeatherAgent`: 1 line with temp + description + emoji. If `city_not_found` in data, guide user to use full city name.
- `CalendarAgent` (`type=calendar_create`): one line — confirmation + title + short weekday + time + 📍 location. The follow-up "¿Quieres más detalles? / Want more details?" is dispatched as a **separate** WhatsApp message by the webhook (reads `result.data["follow_up_message"]`).
- `CalendarAgent` (`type=calendar_next_event`, `calendar_followup`, `calendar_query`): see `FORMATTING_PROMPT` — time + title + 📍 location, with travel + weather for the "next event" variant.
- `AmbiguityAgent`: warm greeting + one clarifying question. Never just an emoji.

**Important:** `FORMATTING_PROMPT.format(lang_name=...)` is inside the try/except block. Never put format strings with `{variable}` in the prompt text — use `[variable]` for examples instead.

**Forbidden:** making routing decisions, calling Firestore or external APIs, returning technical content

---

## Onboarding Flow V1.0.0 (`app/handlers/onboarding_handler.py`)

Runs as an **async** gate BEFORE the 4-layer pipeline. Returns `True` (consumed) or `False` (proceed to pipeline). Onboarding is NOT an agent — it has no `ParsedMessage`, it runs before the parser. All user-facing strings live in `app/handlers/onboarding_copy.py` as bilingual static templates (no LLM in onboarding output — direct WhatsApp sends).

**5-state machine** (stored on `users/{phone}.onboarding_state`):

1. **`language_pending`** — brand-new user → create doc → bilingual prompt with 🇬🇧/🇨🇴 flags. Accepts variations ("english"/"es"/"1"/flag emoji/etc). Spanish check wins first so "en español" → es. Defaults to `en` after one retry.
2. **`profile_pending`** — ask name + city in one message. Uses `app/parser/name_city_extractor.py` (LLM + regex fallback) to extract. Partial answers (only name / only city) loop back asking for the missing piece.
3. **`location_retry`** — fired when `location_resolver` returns `not_found` or `ambiguous`. Asks user to clarify (add country, pick country). Reruns resolver on next message.
4. **`oauth_pending`** — OAuth link sent, waiting for Google callback. Returns `False` for non-calendar messages (Otto still works). Returns `True` for calendar-keyword messages and re-surfaces the link.
5. **`completed`** — returns False → normal pipeline runs.

**Legacy compat:** users with `onboarding_completed=True` and no `onboarding_state` field are treated as `completed` by `_derive_state()`. No migration needed.

**Currency is NOT asked during onboarding** — deferred to first expense. See Hard Rule #13.

**Location resolution** (`app/services/location_resolver.py`): Google Maps Geocoding + Timezone API. Status values: `resolved | not_found | ambiguous | api_error`. On `api_error` the user is NOT blocked — partial state is saved (`location_raw`, `timezone="UTC"`, `location_resolution_status="pending_retry"`), they proceed to OAuth, and `/cron/oauth-followups` retries geocoding later.

**Google Calendar OAuth** (`app/services/google_oauth.py`, `app/api/oauth_routes.py`):
- Per-user refresh tokens, Fernet-encrypted on `users/{phone}.google_calendar_refresh_token` (key: `CALENDAR_TOKEN_ENCRYPTION_KEY`)
- State param is an opaque `secrets.token_urlsafe(32)` stored on the user doc with 1 h expiry, one-time-use (cleared on callback). **Never** the phone number in plaintext.
- Routes: `GET /auth/google/authorize?state=X`, `GET /auth/google/callback`, `GET /auth/done`
- **PKCE flow:** `build_authorize_url()` returns `(url, code_verifier)`. The `code_verifier` is stored in Firestore (`users/{phone}.google_oauth_code_verifier`) at the `/auth/google/authorize` step and passed to `exchange_code()` at the `/auth/google/callback` step. Without this, `google-auth-oauthlib` raises `(invalid_grant) Missing code verifier`.
- Callback exchanges code → refresh_token → encrypt → save → fetch today's events → send WhatsApp confirmation → redirect to `/auth/done`
- `prompt=consent` forced on the authorize URL so Google always returns a refresh_token

**3h follow-up** (`app/api/cron_routes.py`): `POST /cron/oauth-followups` (secret-protected via `X-Cron-Secret` header / `CRON_SHARED_SECRET` env). Called by external cron every ~15 min. Mints a **fresh** state token + 1 h expiry before sending the reminder (the original link is long dead). Also retries any pending location resolutions. Send-once enforced via `oauth_followup_sent_at`.

**Pending expense currency follow-up** (`app/handlers/pending_expense_handler.py`): sibling gate to onboarding. When `ExpenseAgent` returns `needs_currency=True`, the amount/category is stashed in `user_context_store` (in-memory). The next message is intercepted here, parsed for a currency word (COP/USD/EUR/pesos/dolares/etc), finalized via `ExpenseRepository`, and `preferred_currency` is silently locked in.

**Pending event calendar-clarify follow-up** (`app/handlers/pending_event_handler.py`): sibling gate to pending-expense. When `CalendarAgent._handle_clarify_creation` returns `type=calendar_clarify_create`, the extracted event (title/start/location/duration) is stashed in `user_context_store["pending_event"]`. The next message is intercepted here and classified deterministically by `_classify_intent`:
- `affirm` ("sí", "dale", "yes", "create it", ≤6 words) → create the event + send confirmation + send follow-up question
- `query` ("solo ver", "just check") → show today's events instead
- `abort` ("no", "cancela", "nvm") → short ack, drop the stash
- `other` (any longer message) → drop the stash and let the pipeline handle the new topic

Priority is `abort > query > affirm` (so bare "no" always wins over any affirm keyword that happens to co-occur).

---

## Firestore Collections

**`users`** (doc ID = phone number e.g. `+573001234567`):
```
# Core identity
name, language ("es"|"en"), preferred_currency, timezone, location,
latitude, longitude, location_raw, location_resolution_status,

# Onboarding state (V1.0.0)
onboarding_state ("language_pending"|"profile_pending"|"location_retry"|"oauth_pending"|"completed"),
onboarding_completed (legacy mirror, still written),
language_asked_count,

# Google Calendar OAuth (V1.0.0)
google_calendar_refresh_token (Fernet-encrypted),
google_calendar_connected,
google_oauth_state_token, google_oauth_state_expires_at,
google_oauth_code_verifier,          # PKCE verifier — written at /authorize, read at /callback
oauth_link_sent_at, oauth_followup_due_at, oauth_followup_sent_at,

# 1-hour calendar reminders
calendar_reminders_enabled,          # bool — True by default when calendar is connected; explicit False = opted out
notified_event_ids,                  # list[str] of "{eventId}:{YYYY-MM-DD}" — dedupes reminder sends, capped at 100

created_at, updated_at
```
- `location`: normalized "City, Region, Country" from Google Maps Geocoding (used by weather/travel APIs). `location_raw` is the user's exact input, kept for research.
- `preferred_currency`: **not set during onboarding**. First expense with an explicit currency silently locks it in. See Hard Rule #13.
- `timezone`: IANA tz from Google Timezone API. Falls back to `"UTC"` only when geocoding failed and is pending cron retry.
- `language`: "es" | "en" — controls all response language
- `calendar_reminders_enabled`: set to `True` by `/auth/google/callback` right after `save_calendar_credentials`. Treated as True unless explicitly `False` — the reminder cron filters via `UserRepository.list_users_for_reminders()`.
- `notified_event_ids`: event dedup keys use the event's **local** date (user's tz) so a recurring event legitimately fires one reminder per day. `UserRepository.add_notified_event` trims the list to the last 100 entries.

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

**`unknown_messages`** (auto ID — product research log):
```
user_phone_number, raw_message, category, language, onboarding_state,
parsed_signals, routed_to, user_context, created_at
```
- `category`: `"ambiguity" | "location_retry_failed" | "oauth_pending_query" | "error_fallback"`
- `raw_message` is **never** filtered, cleaned, or normalized — the raw input IS the research value
- Written from `AmbiguityAgent`, onboarding handler, and cron retries. No TTL.

---

## File Structure

```
app/
├── api/
│   ├── whatsapp_webhook.py          # Thin dispatcher: verify, normalize, gates, 4-layer pipeline
│   ├── oauth_routes.py              # GET /auth/google/authorize|callback|done
│   └── cron_routes.py               # POST /cron/oauth-followups (secret-protected); also runs location retries + 1h event reminders
├── parser/
│   ├── message_parser.py            # Layer 1: LLM extraction → ParsedMessage
│   ├── word_number_parser.py        # Utility: "dos millones"→2000000, "50 mil"→50000
│   └── name_city_extractor.py       # LLM + regex: extract name + city from onboarding message
├── router/
│   └── deterministic_router.py      # Layer 2: pure keyword routing, no LLM
├── agents/
│   ├── base_agent.py                # Abstract base: execute(parsed, user) -> AgentResult
│   ├── expense_agent.py             # Save expenses, validate category literals
│   ├── calendar_agent.py            # Calendar query, follow-up, event creation, clarify, reminder toggle
│   ├── travel_agent.py              # Maps API leave-time calculation
│   ├── summary_agent.py             # Expense aggregation by date range + currency
│   ├── weather_agent.py             # Weather lookup, city extraction from message
│   ├── greeting_agent.py            # Hardcoded greeting/gratitude responses (no LLM)
│   └── ambiguity_agent.py           # Pass-through + unknown_messages logger
├── responder/
│   └── response_formatter.py        # Layer 4: LLM formats warm WhatsApp message
├── handlers/
│   ├── onboarding_handler.py        # Pre-pipeline gate: 5-state onboarding machine
│   ├── onboarding_copy.py           # Bilingual static strings for onboarding messages
│   ├── pending_expense_handler.py   # Pre-pipeline gate: currency follow-up for stashed expenses
│   └── pending_event_handler.py     # Pre-pipeline gate: calendar-clarify follow-up (affirm/query/abort)
├── repositories/
│   ├── expense_repository.py        # Firestore CRUD for expenses
│   ├── user_repository.py           # Firestore CRUD for users + OAuth/onboarding helpers
│   └── unknown_message_repository.py # Firestore write-only log for product research
├── db/
│   ├── firestore_context_store.py   # Persistent context (Firestore-backed, not used by agents)
│   └── user_context_store.py        # In-memory context (used by CalendarAgent, TravelAgent, pending expense)
├── models/
│   ├── parsed_message.py            # Layer 1 output contract
│   ├── agent_result.py              # Layer 3 output contract
│   ├── extracted_expense.py         # Pydantic model for expense save
│   ├── inbound_message.py           # Normalized incoming WhatsApp message
│   └── webhook_event.py             # Raw webhook payload model
├── services/
│   ├── google_calendar.py           # Calendar API: per-user get_today_events_for_user, create_event_for_user, get_upcoming_events_window (+ legacy global path)
│   ├── google_oauth.py              # build_authorize_url(), exchange_code() — web OAuth flow
│   ├── location_resolver.py         # Google Maps Geocoding + Timezone API; status chain
│   ├── token_crypto.py              # Fernet encrypt/decrypt for stored refresh tokens
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

FIREBASE_CREDENTIALS_JSON           # Raw JSON string of firebase-service-account.json (production/Railway)
                                    # Fallback: FIREBASE_CREDENTIALS_PATH (file path) or credentials/firebase-service-account.json (local)

OPENAI_API_KEY

GOOGLE_MAPS_API_KEY                 # Requires Directions + Geocoding + Timezone APIs enabled in GCP
OPENWEATHER_API_KEY

# Google Calendar OAuth (per-user web flow)
GOOGLE_OAUTH_CLIENT_ID              # Web OAuth 2.0 client ID from GCP Console
GOOGLE_OAUTH_CLIENT_SECRET          # Web OAuth 2.0 client secret from GCP Console
GOOGLE_OAUTH_REDIRECT_URI           # https://<your-domain>/auth/google/callback
PUBLIC_BASE_URL                     # https://<your-domain>
CALENDAR_TOKEN_ENCRYPTION_KEY       # Fernet key — generate once, never rotate

# Cron
CRON_SHARED_SECRET                  # Secret header value for POST /cron/oauth-followups

ENVIRONMENT=development|production
```

**Google Calendar OAuth:** per-user refresh tokens are Fernet-encrypted and stored in Firestore (`users/{phone}.google_calendar_refresh_token`). No `credentials/google_credentials.json` needed at runtime — credentials are read from env vars. The legacy global `get_today_events()` / `get_calendar_service()` (reading `credentials/token.json`) is dead on production — **always use `get_today_events_for_user(refresh_token)`**.

**Firebase on Railway:** use `FIREBASE_CREDENTIALS_JSON` (raw JSON string). `app/core/firebase.py` writes it to a temp file at startup. Do not try to mount a secret file — Railway has no secret file feature.

---

## Hard Rules

1. **Never let the LLM decide routing.** `llm_intent_router.py` is dead. Do not extend it.
2. **Never add structured commands for users.** Natural language only.
3. **Never change Firestore schema** without updating this doc and both repositories.
4. **Never store secrets in code.** Always `.env`.
5. **`whatsapp_webhook.py` is a thin dispatcher.** It verifies, normalizes, runs pre-pipeline gates, and orchestrates the 4 layers — nothing else. Any new pre-pipeline concern belongs in its own `handlers/*.py` file called as a gate, not inlined into the webhook.
6. **Never override user currency** unless the message explicitly states one.
7. **Never return technical errors to users.** Always a graceful human-friendly fallback.
8. **New capability = new Agent in `/agents/`.** Never add feature branches inside the webhook.
9. **Never use `user_context_store.py` for durable data.** It's in-memory. Use Firestore for anything that must survive restarts.
10. **LLM only in Layer 1 (parser) and Layer 4 (responder).** Never in router or agents.
11. **Never put `{variable}` inside `FORMATTING_PROMPT` examples.** Use `[variable]` instead — Python's `.format()` will try to substitute it and crash.
12. **Travel is checked before Calendar in the router.** Do not reorder. "salir para mi reunión" must route to TravelAgent.
13. **Never ask the user for currency during onboarding.** Currency is deferred to the first expense — if the message contains an explicit currency word/symbol it's silently locked in as `preferred_currency`; otherwise `ExpenseAgent` returns `needs_currency=True` and the user is prompted once. Hard Rule #6 still applies thereafter.
14. **CalendarAgent and TravelAgent must always use the per-user refresh token.** Never call `get_today_events()` (legacy global path). Always decrypt `user["google_calendar_refresh_token"]` with `token_crypto.decrypt()` and call `get_today_events_for_user(refresh_token)`. Return `error_message="calendar_not_connected"` if the token is missing.
15. **OAuth PKCE verifier must be stored and retrieved.** `build_authorize_url()` returns `(url, code_verifier)`. Store `code_verifier` in Firestore at `/auth/google/authorize` time. Pass it to `exchange_code(code_verifier=...)` at `/auth/google/callback` time. Omitting this causes `(invalid_grant) Missing code verifier` from Google.

---

## Railway Deployment

**Live URL:** `https://alfredproject-otto-life-co-pilot-production.up.railway.app`

**Builder:** NIXPACKS (configured in `railway.json`). Do not use RAILPACK — it caused proxy routing issues.

**Start command:** `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
- `--host 0.0.0.0` is mandatory — `127.0.0.1` will cause 502s.
- `$PORT` is injected by Railway. Do NOT hardcode it or set `PORT` manually in Railway Variables.

**Networking:** In Railway Settings → Networking, the public domain port must match the port the app binds to (currently **8080**). A mismatch (e.g. domain → 8000, app → 8080) causes `connection refused` on every external request while internal healthchecks still pass.

**Known footguns:**
- `multiRegionConfig` in `railway.json` causes edge proxy misrouting unless you're on an Enterprise plan — keep it out.
- `runtime: "V2"` combined with multi-region caused 502s — removed.
- `dotenv==0.9.9` and `python-dotenv==1.0.1` conflict in the same `requirements.txt` — only keep `python-dotenv`.

**Cron:** APScheduler runs `run_cron_job` every 15 minutes in-process (no external cron service). Defined in `app/main.py` lifespan. `run_cron_job` is synchronous — if it ever starts doing heavy work, move it to a thread pool executor to avoid blocking the event loop.

**What `run_cron_job` does each tick:**
1. **OAuth follow-ups** — for users in `onboarding_state=oauth_pending` past their 3 h due date, mint a fresh state token + PKCE verifier and re-send the authorize link.
2. **Location retries** — re-run `resolve_location` for users whose geocoding hit `api_error` during onboarding.
3. **1-hour event reminders** — for every user in `list_users_for_reminders()` (calendar connected, reminders not explicitly disabled), call `get_upcoming_events_window(token, 55, 75)` and send one WhatsApp reminder per event. Dedup via `notified_event_ids` using `{eventId}:{local_date}`. All-day events (no `start.dateTime`) are skipped. Per-user errors (decrypt failure, Calendar API failure) are logged and do not crash the batch.

---

## Adding a New Agent (checklist)

1. Create `app/agents/your_agent.py` extending `BaseAgent`
2. `execute(parsed: ParsedMessage, user: dict) -> AgentResult`
3. Add keyword set to `deterministic_router.py` and `message_parser.py`
4. Add routing rule in `deterministic_router.py` (respect priority order)
5. Add `_FALLBACKS` and `_ERROR_MESSAGES` entries in `response_formatter.py`
6. Add agent-specific formatting instructions in `FORMATTING_PROMPT`
7. Never touch `whatsapp_webhook.py`
