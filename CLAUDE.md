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

## 4-Layer Architecture

### Request Flow
```
WhatsApp POST /webhook
  -> route_incoming_message()                [services/message_router.py]
  -> map_incoming_event_to_inbound_message() [services/inbound_message_mapper.py]
  -> UserRepository.get_user()               [repositories/user_repository.py]
  -> handle_onboarding()                     [handlers/onboarding_handler.py]  ← async gate
  -> handle_pending_expense()                [handlers/pending_expense_handler.py]  ← currency follow-up gate
  -> handle_pending_event()                  [handlers/pending_event_handler.py]    ← calendar-clarify gate
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
    currency: Optional[str],          # "COP" | "USD" | "EUR" | None
    category_hint: Optional[str],
    date_hint: Optional[str],
    raw_message: str,
    signals: List[str],               # deterministic keyword scan only — never from LLM
    event_reference: Optional[EventReference],
    event_title: Optional[str],       # filled only for NEW event creation
    event_start: Optional[str],       # ISO 8601 w/ tz offset
    event_location: Optional[str],
    event_duration_minutes: Optional[int],
)
```

**Rules:**
- LLM extracts `amount`, `currency`, `category_hint`, `date_hint`, and event fields — nothing else
- `signals` populated by `_scan_signals` — never from LLM
- `parse_word_numbers()` fallback if LLM returns null for amount ("dos millones" → 2000000, "50 mil" → 50000)
- Full heuristic fallback (regex + word_number_parser) if LLM call fails entirely
- `parse_message(raw_text, user_context={"today", "tz"})` — webhook injects today's date and IANA tz so LLM can resolve relative dates
- **Creation-intent safeguard:** if LLM returns `event_title` + `event_start`, parser sets `amount=None` and `event_reference=None` (defeats clock-time-as-amount collision)
- Clock times ("2pm", "las 3", "14:00") are NEVER amounts

**Forbidden:** classifying intent, deciding actions, calling Firestore

---

### Layer 2 — Router (`app/router/deterministic_router.py`)
**Responsibility:** Read `ParsedMessage` → return correct agent. Pure logic, no LLM.

**Routing priority (strict — do not reorder):**
```python
if signal in REMINDER_TOGGLE_KEYWORDS: -> CalendarAgent  # settings, must beat everything
if parsed.amount is not None:          -> ExpenseAgent
if signal in TRAVEL_KEYWORDS:          -> TravelAgent    # before Calendar — "salir para mi reunión"
if signal in WEATHER_KEYWORDS:         -> WeatherAgent
if signal in SUMMARY_KEYWORDS:         -> SummaryAgent   # before Calendar — "have" + "spent" → Summary wins
if signal in CALENDAR_KEYWORDS:        -> CalendarAgent
if signal in CREATE_KEYWORDS:          -> CalendarAgent  # event creation without calendar noun
if parsed.event_reference is not None: -> CalendarAgent  # ordinal follow-ups with no keyword
if signal in GREETING_KEYWORDS:        -> GreetingAgent  # after all functional agents
if signal in GRATITUDE_KEYWORDS:       -> GreetingAgent
else:                                  -> AmbiguityAgent
```

**Keyword sets** (exact values in `deterministic_router.py` and `message_parser.py`):
```python
CALENDAR_KEYWORDS  = {"calendario","agenda","reunion","reunión","meeting","event","evento","tengo","schedule","have","day","busy"}
WEATHER_KEYWORDS   = {"clima","weather","lluvia","temperatura","temperature","rain","calor","frio"}
SUMMARY_KEYWORDS   = {"resumen","summary","cuanto","cuánto","gaste","gasté","spent","gastos","expenses","wasted","waste","spend","money","dinero","plata","gastado"}
TRAVEL_KEYWORDS    = {"llegar","llego","tiempo","tráfico","trafico","traffic","travel","arrive","salir","leave"}
GREETING_KEYWORDS  = {"hola","hello","hey","buenos días","buenos dias","good morning","buenas tardes","good afternoon","buenas noches","good evening","buenas","que tal","qué tal"}
GRATITUDE_KEYWORDS = {"gracias","thanks","thank you","thankss","thanx","grax","tks"}
# All CREATE/REMINDER entries are multi-word phrases — bare verbs collide with benign messages
CREATE_KEYWORDS    = {"agendar","agenda una|un|el|mi","crea/crear una|un|el|mi|la","agregar al calendario",
                       "añade/añadir al calendario","programa(r) una|un","nueva reunión","nuevo evento",
                       "add event|a meeting|an event","create event|meeting|a meeting|an event",
                       "schedule a|an|my","book a|an|me","set up a meeting","new meeting|event",
                       "put it on my calendar","add to my calendar"}
REMINDER_OFF_KEYWORDS = {"recordatorios off","desactivar/desactiva (los) recordatorios","quitar/quita recordatorios",
                          "sin recordatorios","apaga(r) recordatorios","turn off reminders","stop reminders",
                          "disable reminders","mute reminders","no more reminders"}
REMINDER_ON_KEYWORDS  = {"recordatorios on","activar/activa (los) recordatorios","reactivar recordatorios",
                          "encender/enciende recordatorios","turn on reminders","enable reminders",
                          "start reminders","resume reminders"}
REMINDER_TOGGLE_KEYWORDS = REMINDER_OFF_KEYWORDS | REMINDER_ON_KEYWORDS
```

**Key design notes:**
- "hoy"/"today"/"mañana"/"tomorrow" are NOT in `CALENDAR_KEYWORDS` — they're time modifiers, not intent signals
- "hi" excluded from GREETING (substring of "this", "children"). "ty" excluded from GRATITUDE
- `CREATE_KEYWORDS` are multi-word phrases — bare "schedule" would collide with "schedule my gym"

**Forbidden:** calling LLM, calling Firestore, confidence scores

---

### Layer 3 — Agents (`app/agents/`)
**Responsibility:** Execute business logic. Own their Firestore reads/writes.

**Output:** `AgentResult(agent_name: str, success: bool, data: dict, error_message: Optional[str])`

| Agent | File | Responsibility |
|---|---|---|
| `ExpenseAgent` | `expense_agent.py` | Validate amount, normalize currency, save to `expenses` |
| `CalendarAgent` | `calendar_agent.py` | Query/create events, clarify intent, toggle reminders |
| `TravelAgent` | `travel_agent/` (package) | Find next event, Maps API leave time, resolve location, departure reminders |
| `SummaryAgent` | `summary_agent.py` | Expense aggregation by date range + currency |
| `WeatherAgent` | `weather_agent.py` | Fetch weather; extracts city from message if specified |
| `GreetingAgent` | `greeting_agent.py` | Hardcoded responses, no LLM, no Firestore |
| `AmbiguityAgent` | `ambiguity_agent.py` | Phrase-scan for out-of-scope vs true ambiguity; logs to `unknown_messages` |

**Key behaviors:**
- `ExpenseAgent`: category validated against `{"food","transport","shopping","health","other"}`. Non-standard hints fall back to "other" + keyword scan.
- `WeatherAgent`: extracts city from message ("clima en Bogota") and uses it over stored location. `city_not_found: True` in data when 404.
- `SummaryAgent`: handles "hoy"/"esta semana"/"semana pasada"/"este mes"/"mes pasado"/"este año". Default = current week (Mon–now).
- `CalendarAgent` branches (checked in order):
  1. `REMINDER_OFF/ON_KEYWORDS` → `_handle_reminder_toggle` (runs BEFORE `_get_refresh_token` — disconnected users can still toggle)
  2. CREATE keyword + event fields → `_handle_creation` (`type="calendar_create"`, `follow_up_message` dispatched separately)
  3. CREATE keyword without fields → `error_message="missing_event_details"`
  4. Event fields without CREATE → `_handle_clarify_creation` (stash pending event, `type="calendar_clarify_create"`)
  5. `event_reference` → `_handle_followup`
  6. Otherwise → `_handle_query`
- `GreetingAgent`: picks from 3-4 hardcoded options per type/language. Responder short-circuits on `data["response"]`.
- `CalendarAgent` + `TravelAgent`: use in-memory `user_context_store` for ephemeral context (resets on restart — intentional).

**Forbidden:** calling LLM, formatting user-facing text, knowing about WhatsApp

---

### Layer 4 — Responder (`app/responder/response_formatter.py`)
**Responsibility:** Convert `AgentResult` → warm WhatsApp message in the user's language.

**Response paths (checked in order):**
- `GreetingAgent` success → `data["response"]` (no LLM)
- `type="calendar_clarify_create"` → deterministic yes/no via `_build_clarify_message()` (no LLM)
- `type="reminder_opt_out"` / `"reminder_opt_in"` → hardcoded ES/EN copy (no LLM)
- `AmbiguityAgent` + `type="out_of_scope_request"` → hardcoded `_OUT_OF_SCOPE_COPY` (3 variants/lang, no LLM)
- `ExpenseAgent` `needs_currency=True` → currency-ask prompt (no LLM)
- `result.success=False` → `_SPECIFIC_ERRORS` first, then `_ERROR_MESSAGES` per agent (no LLM)
- All other success → GPT-4o-mini with `FORMATTING_PROMPT`; fallback to `_TYPE_FALLBACKS` then `_FALLBACKS`

**Language:** `user["language"]` from Firestore. Prompt + user_content both enforce it.

**Prompt rules per agent:**
- `ExpenseAgent`: max 1 emoji or 1 word. Never repeat amount/category/currency.
- `SummaryAgent`: per-currency lines with thousands separators.
- `WeatherAgent`: 1 line with temp + description + emoji. If `city_not_found`, guide user to use full city name.
- `CalendarAgent` `calendar_create`: one line — confirmation + title + weekday + time + 📍 location. `follow_up_message` dispatched as separate WhatsApp message by webhook.
- `AmbiguityAgent` true ambiguity: warm greeting + one clarifying question. Never just an emoji.

**Critical:** `FORMATTING_PROMPT.format(lang_name=...)` is inside try/except. Never use `{variable}` in prompt examples — use `[variable]` (Python `.format()` will crash).

**Forbidden:** routing decisions, calling Firestore or external APIs, returning technical content

---

## Onboarding Flow (`app/handlers/onboarding_handler.py`)

Async gate BEFORE the pipeline. Returns `True` (consumed) or `False` (proceed). All strings in `onboarding_copy.py` (no LLM).

**5-state machine** (`users/{phone}.onboarding_state`):
1. `language_pending` — new user → create doc → bilingual 🇬🇧/🇨🇴 prompt. Spanish wins on tie. Defaults to `en` after retry.
2. `profile_pending` — ask name + city. `name_city_extractor.py` (LLM + regex). Loops on partial answers.
3. `location_retry` — when `location_resolver` returns `not_found`/`ambiguous`. Reruns on next message.
4. `oauth_pending` — OAuth link sent. Returns `False` for non-calendar msgs. Returns `True` + re-surfaces link for calendar msgs.
5. `completed` — returns `False` → normal pipeline.

**Legacy compat:** `onboarding_completed=True` with no `onboarding_state` → treated as `completed`. No migration needed.

**Location resolution** (`services/location_resolver.py`): Google Maps Geocoding + Timezone API. Statuses: `resolved|not_found|ambiguous|api_error`. On `api_error` user is NOT blocked — partial state saved, cron retries later.

**Google Calendar OAuth** (`services/google_oauth.py`, `api/oauth_routes.py`):
- Per-user Fernet-encrypted refresh tokens. State param = opaque `secrets.token_urlsafe(32)`, 1h expiry, one-time-use.
- PKCE: `build_authorize_url()` returns `(url, code_verifier)`. Store verifier in Firestore at `/authorize`; pass to `exchange_code(code_verifier=...)` at `/callback`. (See Hard Rule #15)
- `prompt=consent` forced so Google always returns a refresh_token.

**Pending expense gate** (`handlers/pending_expense_handler.py`): stashes amount/category in `user_context_store` when `needs_currency=True`. Next message intercepted for currency word → finalize + lock `preferred_currency`.

**Pending event gate** (`handlers/pending_event_handler.py`): stashes pending event on `calendar_clarify_create`. Classifies next reply: `affirm`→create, `query`→show today, `abort`→drop, `other`→drop + pipeline. Priority: `abort > query > affirm`.

**Cron** (`api/cron_routes.py`, APScheduler every 15 min in `app/main.py`):
1. OAuth follow-ups — re-mint fresh state token + PKCE, re-send link (original expired). Send-once via `oauth_followup_sent_at`.
2. Location retries — re-run `resolve_location` for `pending_retry` users.
3. 1h event reminders — `get_upcoming_events_window(token, 55, 75)`. Dedup via `notified_event_ids` (`{eventId}:{local_date}`). Skip all-day events.
4. Morning brief — check local time 06:00–06:14. If `morning_brief_sent_date != today_local` → compose + send (calendar + weather + travel). Write `morning_brief_sent_date`.

---

## Firestore Collections

**`users`** (doc ID = phone number `+573001234567`):
```
name, language ("es"|"en"), preferred_currency, timezone, location,
latitude, longitude, location_raw, location_resolution_status,
onboarding_state, onboarding_completed (legacy),  language_asked_count,
google_calendar_refresh_token (Fernet), google_calendar_connected,
google_oauth_state_token, google_oauth_state_expires_at, google_oauth_code_verifier,
oauth_link_sent_at, oauth_followup_due_at, oauth_followup_sent_at,
calendar_reminders_enabled,   # True by default on connect; explicit False = opted out
notified_event_ids,           # list[str] "{eventId}:{YYYY-MM-DD}" — capped at 100
morning_brief_sent_date,      # "YYYY-MM-DD" user local tz
created_at, updated_at
```
- `location`: normalized "City, Region, Country" from Geocoding. `location_raw` = user's raw input.
- `preferred_currency`: not set during onboarding. Locked on first expense with explicit currency.
- `timezone`: IANA tz. Falls back to `"UTC"` only on geocoding failure (pending retry).
- `notified_event_ids`: keyed by local date so recurring events get one reminder per day.

**`expenses`** (auto ID): `user_phone_number, amount, currency, category, confidence, user_message, source, created_at`
- `category`: `"food"|"transport"|"shopping"|"health"|"other"`. `source`: always `"whatsapp user's chat"`.

**`user_context`**: exists but NOT used by agents. Agents use in-memory `user_context_store.py`.

**`unknown_messages`** (auto ID): `user_phone_number, raw_message, category, language, onboarding_state, parsed_signals, routed_to, user_context, created_at`
- `category`: `"ambiguity"|"capability_request"|"location_retry_failed"|"oauth_pending_query"|"error_fallback"`

**`scheduled_reminders`** (auto ID): `user_phone_number, type, event_title, event_location, event_start_iso, fire_at, lang, created_at`
- `type`: `"departure"` (reserved for future reminder types).
- `fire_at`: ISO 8601 tz-aware string. Cron matches reminders where `fire_at` is in `[now - 5min, now + 15min]`.
- Docs are **deleted** after delivery — every doc in the collection is pending by definition. No accumulation.
- Written by `ScheduleDepartureReminderSkill`. Delivered and deleted by `_run_departure_reminders()` in `cron_routes.py`.

---

## File Structure

```
app/
├── api/
│   ├── whatsapp_webhook.py          # Thin dispatcher: verify, normalize, gates, 4-layer pipeline
│   ├── oauth_routes.py              # /auth/google/authorize|callback|done
│   └── cron_routes.py               # /cron/oauth-followups (X-Cron-Secret protected)
├── parser/
│   ├── message_parser.py            # Layer 1
│   ├── word_number_parser.py        # "dos millones"→2000000
│   └── name_city_extractor.py       # Onboarding name+city extraction
├── router/
│   └── deterministic_router.py      # Layer 2
├── agents/
│   ├── base_agent.py
│   ├── expense_agent.py
│   ├── calendar_agent.py
│   ├── travel_agent/            # Agent/Skill package — reference implementation (see OTTO_AGENTS.md)
│   ├── summary_agent.py
│   ├── weather_agent.py
│   ├── greeting_agent.py
│   └── ambiguity_agent.py
├── responder/
│   └── response_formatter.py        # Layer 4
├── handlers/
│   ├── onboarding_handler.py
│   ├── onboarding_copy.py           # Bilingual static strings
│   ├── pending_expense_handler.py
│   └── pending_event_handler.py
├── repositories/
│   ├── expense_repository.py
│   ├── user_repository.py
│   └── unknown_message_repository.py
├── db/
│   ├── firestore_context_store.py   # Firestore-backed, NOT used by agents
│   └── user_context_store.py        # In-memory ephemeral context
├── models/
│   ├── parsed_message.py
│   ├── agent_result.py
│   ├── extracted_expense.py
│   ├── inbound_message.py
│   └── webhook_event.py
├── services/
│   ├── google_calendar.py           # get_today_events_for_user, create_event_for_user, get_upcoming_events_window
│   ├── google_oauth.py              # build_authorize_url(), exchange_code()
│   ├── location_resolver.py
│   ├── token_crypto.py              # Fernet encrypt/decrypt
│   ├── maps/maps_service.py
│   ├── weather/weather_service.py
│   ├── morning_brief/
│   ├── message_router.py
│   ├── inbound_message_mapper.py
│   └── whatsapp_sender.py
├── scripts/
│   ├── reauthorize_calendar.py
│   └── run_morning_brief.py
├── core/
│   └── firebase.py
└── main.py

# DEAD — do not extend:
app/routers/llm_intent_router.py     # LLM routing violation
app/services/intent_classifier.py
app/services/expense_extractor.py
app/ai/ai_service.py
app/services/response_service.py
```

---

## Environment Variables

```
WHATSAPP_ACCESS_TOKEN
WHATSAPP_PHONE_NUMBER_ID
WHATSAPP_VERIFY_TOKEN
FIREBASE_CREDENTIALS_JSON           # Raw JSON string (Railway). Fallback: FIREBASE_CREDENTIALS_PATH or credentials/firebase-service-account.json
OPENAI_API_KEY
GOOGLE_MAPS_API_KEY                 # Requires Directions + Geocoding + Timezone APIs
OPENWEATHER_API_KEY
GOOGLE_OAUTH_CLIENT_ID
GOOGLE_OAUTH_CLIENT_SECRET
GOOGLE_OAUTH_REDIRECT_URI           # https://<domain>/auth/google/callback
PUBLIC_BASE_URL
CALENDAR_TOKEN_ENCRYPTION_KEY       # Fernet key — generate once, never rotate
CRON_SHARED_SECRET
ENVIRONMENT=development|production
```

**Firebase on Railway:** use `FIREBASE_CREDENTIALS_JSON`. `firebase.py` writes it to a temp file at startup. Always use `get_today_events_for_user(refresh_token)` — never the legacy `get_today_events()`.

---

## Hard Rules

1. **Never let the LLM decide routing.** `llm_intent_router.py` is dead.
2. **Never add structured commands for users.** Natural language only.
3. **Never change Firestore schema** without updating this doc and both repositories.
4. **Never store secrets in code.** Always `.env`.
5. **`whatsapp_webhook.py` is a thin dispatcher.** New pre-pipeline concerns → own `handlers/*.py` gate.
6. **Never override user currency** unless the message explicitly states one.
7. **Never return technical errors to users.** Always a graceful human-friendly fallback.
8. **New capability = new Agent in `/agents/`.** Never add feature branches inside the webhook.
9. **Never use `user_context_store.py` for durable data.** It's in-memory.
10. **LLM only in Layer 1 (parser) and Layer 4 (responder).** Never in router or agents.
11. **Never put `{variable}` inside `FORMATTING_PROMPT` examples.** Use `[variable]` instead.
12. **Travel is checked before Calendar in the router.** Do not reorder.
13. **Never ask the user for currency during onboarding.** Deferred to first expense. `ExpenseAgent` returns `needs_currency=True` if no explicit currency. Hard Rule #6 applies thereafter.
14. **CalendarAgent and TravelAgent must always use the per-user refresh token.** Never `get_today_events()`. Decrypt with `token_crypto.decrypt()`. Return `error_message="calendar_not_connected"` if token missing.
15. **OAuth PKCE verifier must be stored and retrieved.** `build_authorize_url()` returns `(url, code_verifier)`. Store in Firestore at `/authorize`, pass to `exchange_code(code_verifier=...)` at `/callback`. Omitting causes `(invalid_grant) Missing code verifier`.

---

## Railway Deployment

**Live URL:** `https://alfredproject-otto-life-co-pilot-production.up.railway.app`
**Builder:** NIXPACKS (`railway.json`). Do not use RAILPACK.
**Start:** `uvicorn app.main:app --host 0.0.0.0 --port $PORT` — `0.0.0.0` is mandatory, `$PORT` injected by Railway.
**Networking:** public domain port must match app bind port (8080). Mismatch → `connection refused` externally.

**Footguns:**
- `multiRegionConfig` in `railway.json` → edge proxy misrouting (Enterprise only). Keep it out.
- `dotenv==0.9.9` + `python-dotenv==1.0.1` conflict — only keep `python-dotenv`.

---

## Git Branching Strategy

| Work type | Branch from | Branch name | PR target |
|---|---|---|---|
| New feature | `develop` | `feature/description` | `develop` |
| Hotfix | `main` | `hotfix/description` | `main` AND `develop` |
| Release | — | PR `develop` → `main` | `main` |

- Never commit directly to `main` or `develop`
- Format: `type: description` (`feat`, `fix`, `chore`, `docs`)
- Push branch → Jilmar opens PR manually on GitHub

---

## Adding a New Agent (checklist)

**New agents must follow the Agent/Skill package pattern. See `OTTO_AGENTS.md` for the full spec and checklists.**

For new agents (package pattern):
1. Create `app/agents/<domain>/` package — see `OTTO_AGENTS.md` § "Adding a New Agent".
2. Existing flat agents (Expense, Calendar, Summary, Weather, Greeting, Ambiguity) migrate later; do not refactor them as part of adding a new agent.

For reference (flat-file pattern, existing agents only):
1. Create `app/agents/your_agent.py` extending `BaseAgent`
2. `execute(parsed: ParsedMessage, user: dict) -> AgentResult`
3. Add keyword set to `deterministic_router.py` and `message_parser.py`
4. Add routing rule in `deterministic_router.py` (respect priority order)
5. Add `_FALLBACKS` and `_ERROR_MESSAGES` entries in `response_formatter.py`
6. Add agent-specific formatting instructions in `FORMATTING_PROMPT`
7. Never touch `whatsapp_webhook.py`
