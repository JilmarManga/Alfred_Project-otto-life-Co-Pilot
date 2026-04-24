# OTTO_ENGINEERING.md
> Canonical technical reference for Otto (codename Alfred).
> Last updated: April 2026 — Beta launch
> Maintained by: Claude Code (CTO project)

---

## 1. System Overview

Otto is a WhatsApp-native AI assistant. Users send natural language messages ("Pagué 50 mil de almuerzo", "¿A qué hora debo salir?") and receive warm, action-confirmed replies. It is **not a chatbot** — it's a deterministic action system with probabilistic language understanding.

**Core design axiom:** LLM is a parser, not an orchestrator. The LLM extracts structured data from text. A deterministic router decides what happens with it.

**Production URL:** `https://alfredproject-otto-life-co-pilot-production.up.railway.app`
**Codebase:** `~/Desktop/Projects/alfred-backend`
**Infrastructure:** Railway (NIXPACKS), auto-deploys on `main` push

---

## 2. Tech Stack

| Layer | Technology | Notes |
|---|---|---|
| Runtime | Python 3.13 | |
| Framework | FastAPI + Uvicorn | `--host 0.0.0.0 --port $PORT` mandatory |
| Data | Firestore (Google Cloud) | No SQL anywhere |
| AI/NLP | OpenAI GPT-4o-mini | Layer 1 (parse) + Layer 4 (format) only |
| Messaging | WhatsApp Cloud API (Meta) | Webhook POST + send via REST |
| Calendar | Google Calendar API | Per-user OAuth 2.0 + PKCE |
| Maps | Google Maps Directions + Geocoding + Timezone API | |
| Weather | OpenWeatherMap API | Language-aware |
| Auth | Fernet encryption + OAuth 2.0 PKCE | Tokens encrypted at rest in Firestore |
| Infra | Railway (NIXPACKS) | No RAILPACK — caused proxy routing bugs |
| Scheduling | APScheduler in-process | Runs inside FastAPI lifespan, every 15 min |
| Secrets | Environment variables only | Never in code or files |

---

## 3. The 4-Layer Pipeline (Architectural Law)

```
WhatsApp POST /webhook
  → route_incoming_message()                [services/message_router.py]
  → map_incoming_event_to_inbound_message() [services/inbound_message_mapper.py]
  → UserRepository.get_user()               [repositories/user_repository.py]
  → handle_onboarding()       ← GATE 1      [handlers/onboarding_handler.py]
  → handle_pending_expense()  ← GATE 2      [handlers/pending_expense_handler.py]
  → handle_pending_event()    ← GATE 3      [handlers/pending_event_handler.py]
  → handle_pending_travel()   ← GATE 4      [handlers/pending_travel_handler.py]
  → parse_message()           LAYER 1       [parser/message_parser.py]
  → route()                   LAYER 2       [router/deterministic_router.py]
  → agent.execute()           LAYER 3       [agents/*.py]
  → format_response()         LAYER 4       [responder/response_formatter.py]
  → send_whatsapp_message()                 [services/whatsapp_sender.py]
```

### Layer 1 — Parser (`app/parser/message_parser.py`)
**Contract:** `parse_message(raw_text, user_context) → ParsedMessage`

Calls GPT-4o-mini to extract structured data. The LLM's job is limited to field extraction — it **never classifies intent**.

**ParsedMessage fields:**
```python
amount: Optional[float]
currency: Optional[str]           # "COP" | "USD" | "EUR" | None
category_hint: Optional[str]
date_hint: Optional[str]
raw_message: str
signals: List[str]                 # ALWAYS from deterministic keyword scan — never LLM
event_reference: Optional[EventReference]
event_title: Optional[str]        # only when describing a NEW event
event_start: Optional[str]        # ISO 8601 with tz offset
event_location: Optional[str]
event_duration_minutes: Optional[int]
```

**Key behaviors:**
- `signals` populated by `_scan_signals()` — deterministic keyword scan, never LLM output
- `parse_word_numbers()` fallback if LLM returns null amount ("dos millones" → 2000000)
- `word_number_parser.py` handles digit+multiplier: "50 mil" → 50000, "2 millones" → 2000000
- Full heuristic fallback (regex + word_number_parser) if the LLM call fails entirely
- `user_context` injects `today` (date) and `tz` (IANA timezone) so LLM resolves "mañana" → absolute ISO datetime
- **Creation-intent safeguard:** if LLM returns `event_title` + `event_start`, parser forces `amount=None` and `event_reference=None` — prevents clock times ("2pm") from being parsed as amounts
- Clock times ("2pm", "las 3", "14:00") are never amounts — enforced by prompt + safeguard

**Forbidden:** intent classification, routing decisions, Firestore calls

---

### Layer 2 — Router (`app/router/deterministic_router.py`)
**Contract:** `route(parsed: ParsedMessage) → Agent instance`

Pure deterministic logic. No LLM. No I/O. Reads signals and parsed fields, returns an agent.

**Routing priority (strict order — never reorder without documented reason):**
```
1. REMINDER_TOGGLE_KEYWORDS  → CalendarAgent    # settings — must win everything
2. parsed.amount is not None → ExpenseAgent
3. TRAVEL_KEYWORDS           → TravelAgent      # before Calendar — "salir para mi reunión"
4. WEATHER_KEYWORDS          → WeatherAgent
5. SUMMARY_KEYWORDS          → SummaryAgent     # before Calendar — "spent" beats "have"
6. CALENDAR_KEYWORDS         → CalendarAgent
7. CREATE_KEYWORDS           → CalendarAgent    # creation intent without calendar noun
8. event_reference not None  → CalendarAgent    # ordinal follow-ups with no keyword
9. GREETING_KEYWORDS         → GreetingAgent    # social signals after all functional agents
10. GRATITUDE_KEYWORDS       → GreetingAgent
11. else                     → AmbiguityAgent
```

**Critical routing notes:**
- "hoy", "today", "mañana", "tomorrow" are NOT calendar keywords — they are time modifiers
- "hi" is NOT a greeting keyword — substring of "this", "children", etc.
- "ty" is NOT a gratitude keyword — too short, false positives
- All REMINDER and CREATE keywords are multi-word phrases to avoid bare-word collisions
- Summary checked before Calendar because "have" + "spent" → SummaryAgent must win

**Forbidden:** LLM calls, Firestore calls, confidence scores

---

### Layer 3 — Agents (`app/agents/`)
**Contract:** `execute(parsed: ParsedMessage, user: dict) → AgentResult`

```python
AgentResult(agent_name: str, success: bool, data: dict, error_message: Optional[str])
```

| Agent | Location | Pattern | Domain |
|---|---|---|---|
| ExpenseAgent | `expense_agent.py` | flat (legacy) | Validate + save expenses to Firestore |
| CalendarAgent | `calendar_agent.py` | flat (legacy) | Query/create events, reminder toggle, follow-ups |
| TravelAgent | `travel_agent/` (package) | **package** | Leave time, location resolution, departure reminders |
| SummaryAgent | `summary_agent.py` | flat (legacy) | Expense aggregation by date range + currency |
| WeatherAgent | `weather_agent.py` | flat (legacy) | OpenWeatherMap fetch, city extraction |
| GreetingAgent | `greeting_agent.py` | flat (legacy) | Hardcoded greeting/gratitude — no LLM, no Firestore |
| AmbiguityAgent | `ambiguity_agent.py` | flat (legacy) | Logs to `unknown_messages`, detects capability requests |

TravelAgent is the **reference implementation** of the new Agent/Skill package pattern (see `OTTO_AGENTS.md`). All future agents use the package pattern. Flat agents migrate one at a time when they gain a new Skill.

**CalendarAgent internal branches (checked in this order):**
1. REMINDER_TOGGLE → `_handle_reminder_toggle` (no Calendar API needed)
2. CREATE keyword + event fields → `_handle_creation` → `create_event_for_user()`
3. CREATE keyword without fields → `error_message="missing_event_details"`
4. event fields without CREATE → `_handle_clarify_creation` → stash in `user_context_store`
5. event_reference → `_handle_followup`
6. Otherwise → `_handle_query` (today's events)

**ExpenseAgent categories (Pydantic Literal):**
`"food" | "transport" | "shopping" | "health" | "other"`
Non-standard hints (e.g. "rent", "housing") fall back to "other" then keyword scan.

**SummaryAgent date ranges:** "hoy", "esta semana" (Mon–now, default), "semana pasada", "este mes", "mes pasado", "este año"

**Forbidden:** LLM calls, user-facing text formatting, knowledge of WhatsApp

---

### Layer 4 — Responder (`app/responder/response_formatter.py`)
**Contract:** `format_response(result: AgentResult, user: dict) → str`

Converts AgentResult into a warm WhatsApp message in the user's language.

**Response path decision tree (in order):**
1. GreetingAgent + success → hardcoded `data["response"]` — no LLM
2. `type="calendar_clarify_create"` → deterministic yes/no question — no LLM
3. `type="reminder_opt_out|reminder_opt_in"` → hardcoded copy — no LLM
4. AmbiguityAgent + `type="out_of_scope_request"` → hardcoded copy (3 variants per language) — no LLM
5. ExpenseAgent + `needs_currency=True` → deterministic currency-ask — no LLM
6. `result.success=False` → specific error map, then per-agent fallback — no LLM
7. All other success cases → GPT-4o-mini with `FORMATTING_PROMPT` + language enforcement

**Language enforcement:** `FORMATTING_PROMPT` includes "You MUST respond in {lang_name} ONLY". `user["language"]` drives it ("es" | "en").

**Critical implementation note:** `FORMATTING_PROMPT.format(lang_name=...)` uses Python `.format()`. Never put `{variable}` in prompt example text — use `[variable]` instead or it crashes.

**Forbidden:** routing decisions, Firestore calls, external API calls

---

## 4. Pre-Pipeline Gates

Three async gates run before the 4-layer pipeline. Each returns `True` (message consumed) or `False` (proceed to pipeline).

### Gate 1 — Onboarding (`handlers/onboarding_handler.py`)
5-state machine stored on `users/{phone}.onboarding_state`:

| State | Trigger | What happens |
|---|---|---|
| `language_pending` | New user | Send bilingual prompt, detect es/en, create user doc |
| `profile_pending` | Language set | Ask name + city, extract via LLM + regex fallback |
| `location_retry` | Geocoding returned `not_found` or `ambiguous` | Ask for clarification, re-run resolver |
| `oauth_pending` | Profile complete | Send OAuth link, return False for non-calendar messages |
| `completed` | OAuth done | Return False — normal pipeline |

**Beta gate:** An additional `beta_pending` state exists — new users must confirm to join beta before onboarding continues.

**Legacy compat:** Users with `onboarding_completed=True` but no `onboarding_state` are treated as `completed`. No migration needed.

### Gate 2 — Pending Expense (`handlers/pending_expense_handler.py`)
When `ExpenseAgent` returns `needs_currency=True`, the amount + category are stashed in `user_context_store` (in-memory). Next message is intercepted here, parsed for a currency word (COP/USD/EUR/pesos/dólares), finalized, and `preferred_currency` is silently locked in on `users/{phone}`.

### Gate 3 — Pending Event (`handlers/pending_event_handler.py`)
When `CalendarAgent._handle_clarify_creation` runs, the extracted event is stashed in `user_context_store["pending_event"]`. Next message is intercepted and classified:
- `affirm` ("sí", "dale", "yes", ≤6 words) → create event
- `query` ("solo ver", "just check") → show today's events
- `abort` ("no", "cancela", "nvm") → drop stash
- `other` (longer message) → drop stash, let pipeline handle new topic

Priority: abort > query > affirm

### Gate 4 — Pending Travel (`handlers/pending_travel_handler.py`)
Two-step state machine stored in `user_context_store[phone]["pending_travel"]`.

**Step 1 — `awaiting_location`:** TravelAgent stashes this when an event has no location. The user's next short reply is treated as a place name → geocoded → leave time computed → stash advanced to step 2. A long message or multi-topic reply drops the stash and falls through to the pipeline.

**Step 2 — `awaiting_reminder_confirmation`:** Otto has offered a departure reminder. Next reply classified:
- `affirm` ("sí", "dale", "ok") → `ScheduleDepartureReminderSkill` → doc written to `scheduled_reminders`
- `abort` ("no", "cancel") → drop stash, send ack
- `other` / long message → drop stash, fall through

Uses word-boundary regex for single-word abort keywords to prevent false positives (e.g. "no" inside "andino").

---

## 5. Firestore Schema

### `users` (doc ID = phone number e.g. `+573001234567`)
```
# Identity
name, language ("es"|"en"), preferred_currency, timezone (IANA),
location (normalized "City, Region, Country"), latitude, longitude,
location_raw (user's exact input), location_resolution_status,

# Onboarding
onboarding_state: "language_pending"|"profile_pending"|"location_retry"|"beta_pending"|"oauth_pending"|"completed"
onboarding_completed (legacy bool — still written for compat),
language_asked_count, beta_pending_sent_at,

# Google Calendar
google_calendar_refresh_token (Fernet-encrypted string),
google_calendar_connected (bool),
google_oauth_state_token (opaque random, 1h expiry, one-time-use),
google_oauth_state_expires_at,
google_oauth_code_verifier (PKCE verifier — written at /authorize, read at /callback),
oauth_link_sent_at, oauth_followup_due_at, oauth_followup_sent_at,

# Reminders + Morning Brief
calendar_reminders_enabled (bool — True by default on connect, False = opted out),
notified_event_ids (list[str] of "{eventId}:{YYYY-MM-DD}", capped at 100),
morning_brief_sent_date ("YYYY-MM-DD" in user's local tz),

created_at, updated_at
```

### `expenses` (auto ID)
```
user_phone_number, amount (float), currency ("COP"|"USD"|"EUR"),
category ("food"|"transport"|"shopping"|"health"|"other"),
confidence (float), user_message (raw), source ("whatsapp user's chat"),
created_at
```

### `user_context` (doc ID = phone number)
Firestore-backed context store (`db/firestore_context_store.py`) — **exists but is not used by agents**. Present for future use.

### `unknown_messages` (auto ID — product research log)
```
user_phone_number, raw_message (never normalized — the research value),
category ("ambiguity"|"capability_request"|"location_retry_failed"|"oauth_pending_query"|"error_fallback"),
language, onboarding_state, parsed_signals, routed_to, user_context,
created_at
```
No TTL. Used for product analytics and capability discovery.

### `scheduled_reminders` (auto ID)
```
user_phone_number, type ("departure"), event_title, event_location,
event_start_iso, fire_at (ISO 8601 tz-aware), lang ("es"|"en"), created_at
```
Written by `ScheduleDepartureReminderSkill` when a user confirms a departure reminder.
**Docs are deleted after delivery** — no accumulation. Every doc in the collection is pending by definition.
Cron matches reminders where `fire_at ∈ [now − 5min, now + 15min]` and delivers + deletes them.

---

## 6. Security Architecture

### Token Storage
- All Google Calendar refresh tokens are **Fernet-encrypted at rest** in Firestore
- Encryption key: `CALENDAR_TOKEN_ENCRYPTION_KEY` env var — generated once, never rotated
- `app/services/token_crypto.py` wraps encrypt/decrypt

### OAuth 2.0 + PKCE Flow
```
1. /auth/google/authorize
   - Generate opaque state token (secrets.token_urlsafe(32)) + 1h expiry → save to Firestore
   - Generate PKCE code_verifier → save to Firestore (users/{phone}.google_oauth_code_verifier)
   - build_authorize_url() returns (url, code_verifier)
   - Redirect user to Google with prompt=consent (forces refresh_token return)

2. /auth/google/callback
   - Validate state token (must match Firestore, not expired, one-time-use → clear after read)
   - Retrieve code_verifier from Firestore
   - exchange_code(code_verifier=...) → get tokens
   - Fernet-encrypt refresh_token → save to Firestore
   - Clear code_verifier from Firestore
   - Send WhatsApp confirmation → redirect to /auth/done
```

**State token invariants:**
- Never the phone number in plaintext in the state param
- 1-hour expiry enforced server-side
- One-time-use: cleared immediately on callback
- Cron mints a **fresh** state token before sending follow-up reminders (original is long dead)

### Secrets Management
- All secrets via environment variables only — never in code
- `FIREBASE_CREDENTIALS_JSON`: raw JSON string (Railway has no secret file feature)
- `app/core/firebase.py` writes it to a temp file at startup

### Prompt Injection Risk
- User input flows into Layer 1 (LLM parsing) — potential injection vector
- Mitigation: the LLM is given a tightly scoped extraction prompt, not a conversational role
- The LLM output is validated deterministically before any action is taken (signals from keyword scan, not LLM output)
- **Gap:** no explicit sanitization of user input before LLM call — a sophisticated injection could attempt to manipulate field extraction

### Logging Safety
- `unknown_messages.raw_message` stores raw user input — intentional for research
- Must never log decrypted tokens, API keys, or user PII beyond what's in Firestore schema

---

## 7. Infrastructure

### Railway Deployment
- Builder: NIXPACKS — do not switch to RAILPACK (caused proxy routing issues)
- Start: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
  - `--host 0.0.0.0` mandatory — 127.0.0.1 causes 502s
  - `$PORT` injected by Railway — never hardcode, never set manually
- Public domain port must match app bind port in Railway Networking settings
- Auto-deploys on `main` branch push
- Known footguns:
  - `multiRegionConfig` in railway.json causes edge proxy misrouting (Enterprise-only feature)
  - `runtime: "V2"` + multi-region → 502s
  - `dotenv==0.9.9` + `python-dotenv==1.0.1` conflict — only keep `python-dotenv`

### APScheduler Cron (in-process)
Runs every 15 minutes inside the FastAPI lifespan (`app/main.py`). Single instance — no distributed locking. Synchronous function — must not do heavy I/O without a thread pool executor.

**Each cron tick does:**
1. **OAuth follow-ups** — users in `oauth_pending` past 3h due date → mint fresh state + PKCE → re-send link
2. **Location retries** — users with `location_resolution_status=pending_retry` → re-run geocoding
3. **1-hour event reminders** — for `list_users_for_reminders()` (calendar connected + reminders not explicitly disabled) → `get_upcoming_events_window(token, 55, 75)` → send one reminder per event → dedup via `notified_event_ids` using `{eventId}:{local_date}` key
4. **Morning brief** — for `list_users_for_morning_brief()` → check local time 06:00–06:14 → if `morning_brief_sent_date != today_local` → compose (events + weather + travel to first event) → send → write `morning_brief_sent_date`
5. **Departure reminders** — query `scheduled_reminders` where `fire_at ∈ [now − 5min, now + 15min]` → send WhatsApp → delete doc (no accumulation)

**Per-user errors in cron are caught and logged — they do not crash the batch.**

### Conversational Context Store
`app/db/user_context_store.py` — in-memory Python dict. Used by CalendarAgent, TravelAgent, pending expense gate, pending event gate for short-lived follow-up context. **Intentionally ephemeral** — resets on server restart. Do not use for durable state.

---

## 8. Google Calendar Integration

- **Per-user OAuth** — no global service account for calendar. Each user authenticates independently.
- Always use: `get_today_events_for_user(refresh_token)` and `create_event_for_user(refresh_token, ...)`
- Never use: `get_today_events()` (legacy global path reading `credentials/token.json`) — dead on production
- `get_upcoming_events_window(token, min_minutes, max_minutes)` — used by cron for reminders
- All-day events (no `start.dateTime`) are skipped by the reminder cron
- `notified_event_ids` dedup key: `{eventId}:{local_date}` — same event can fire once per calendar day (correct for recurring events)
- List is capped at 100 entries by `UserRepository.add_notified_event()`

---

## 9. Onboarding Details

**Location Resolution (`services/location_resolver.py`):**
Uses Google Maps Geocoding + Timezone API.
Status values: `resolved | not_found | ambiguous | api_error`
On `api_error`: user is NOT blocked — partial state saved (`location_raw`, `timezone="UTC"`, `location_resolution_status="pending_retry"`), they proceed to OAuth, cron retries geocoding later.

**Name + City Extraction (`parser/name_city_extractor.py`):**
LLM extraction with regex fallback. Handles partial answers — loops back asking for missing piece.

**Currency NOT collected during onboarding.** First expense with explicit currency locks in `preferred_currency` silently. If no currency, ExpenseAgent returns `needs_currency=True` → Gate 2 intercepts next message.

---

## 10. Known Technical Debt & Risks

### High Priority
| Issue | Risk | Notes |
|---|---|---|
| In-memory context store | State lost on restart | Fine for single-instance Railway, becomes a bug if multiple instances or restarts under load |
| APScheduler in-process | No distributed safety | If Railway ever scales to 2 instances, cron runs twice — duplicate reminders, double OAuth follow-ups |
| No test suite | Regression risk | No unit or integration tests exist. All validation is manual/prod. |
| No observability | Debugging blind | No Sentry, no structured logging, no alerting. Failures visible only in Railway logs. |
| Prompt injection gap | Security | User input not sanitized before LLM call in Layer 1 |

### Medium Priority
| Issue | Risk | Notes |
|---|---|---|
| OpenAI rate limits | Availability | No retry logic with backoff on LLM calls |
| Firestore read costs | Cost at scale | SummaryAgent and reminder cron do full user scans — expensive at 10K+ users |
| No staging environment | Deploy risk | All changes go directly to production after PR merge to main |
| `unknown_messages` has no TTL | Storage growth | Unbounded collection — needs pruning strategy at scale |
| GPT-4o-mini for formatting | Quality | Works well now; at scale, latency and cost may push toward caching common responses |

### Low Priority
| Issue | Notes |
|---|---|
| Legacy dead files | `llm_intent_router.py`, `intent_classifier.py`, `expense_extractor.py`, `ai_service.py`, `response_service.py` still in repo |
| `user_context` Firestore collection | Exists but unused — either wire it up or drop it |
| `morning_brief_sent_date` is a string | Should be a Firestore Timestamp for consistency |

---

## 11. Scaling Considerations

| User count | What breaks |
|---|---|
| Current (~0–500) | Nothing — Railway single instance handles this comfortably |
| ~1,000 users | Cron batch takes longer; in-memory context store still fine (single instance) |
| ~5,000 users | Cron starts blocking event loop; SummaryAgent/reminder scans become expensive Firestore reads; need APScheduler → Cloud Tasks or a job queue |
| ~10,000 users | Railway auto-scaling kicks in → multiple instances → in-memory context store breaks (Gate 2 and 3 lose state across instances); must migrate to Firestore-backed context |
| ~50,000 users | Firestore read costs become significant; user scan queries need indexes + pagination; OpenAI costs become meaningful |
| ~100,000+ users | Consider moving off Railway to GCP Cloud Run; Firestore may need sharding strategy; WhatsApp API rate limits become relevant; need proper observability (Sentry + metrics) |

**Bottlenecks to instrument first:** LLM call latency (Layer 1 + Layer 4), Firestore reads per request, cron batch duration.

---

## 12. Environment Variables Reference

```
# WhatsApp
WHATSAPP_ACCESS_TOKEN
WHATSAPP_PHONE_NUMBER_ID
WHATSAPP_VERIFY_TOKEN

# Firebase
FIREBASE_CREDENTIALS_JSON           # Raw JSON string — Railway has no secret file feature
FIREBASE_CREDENTIALS_PATH           # Fallback (local dev)

# AI
OPENAI_API_KEY

# Maps + Weather
GOOGLE_MAPS_API_KEY                 # Requires: Directions + Geocoding + Timezone APIs
OPENWEATHER_API_KEY

# Google Calendar OAuth
GOOGLE_OAUTH_CLIENT_ID
GOOGLE_OAUTH_CLIENT_SECRET
GOOGLE_OAUTH_REDIRECT_URI           # https://<domain>/auth/google/callback
PUBLIC_BASE_URL                     # https://<domain>
CALENDAR_TOKEN_ENCRYPTION_KEY       # Fernet key — generate once, never rotate

# Cron
CRON_SHARED_SECRET                  # X-Cron-Secret header for POST /cron/oauth-followups

ENVIRONMENT                         # "development" | "production"
```

---

## 13. Key Files Reference

```
app/
├── api/
│   ├── whatsapp_webhook.py          # Entry point — thin dispatcher only
│   ├── oauth_routes.py              # /auth/google/authorize|callback|done
│   └── cron_routes.py               # /cron/oauth-followups (secret-protected)
├── parser/
│   ├── message_parser.py            # Layer 1 — LLM extraction → ParsedMessage
│   ├── word_number_parser.py        # "dos millones"→2000000, "50 mil"→50000
│   └── name_city_extractor.py       # Onboarding: LLM + regex name+city extraction
├── router/
│   └── deterministic_router.py      # Layer 2 — pure keyword routing
├── agents/                          # Layer 3
│   ├── base_agent.py
│   ├── travel_agent/                # Package pattern (reference implementation)
│   │   ├── __init__.py              # re-exports TravelAgent
│   │   ├── agent.py                 # _SKILLS registry + execute + run_skill + _run
│   │   ├── skill_context.py         # SkillContext (frozen) + SkillResult
│   │   ├── skills/
│   │   │   ├── base.py              # TravelSkill ABC
│   │   │   ├── next_event_travel.py
│   │   │   ├── resolve_event_location.py
│   │   │   └── schedule_departure_reminder.py
│   │   └── _shared/
│   │       ├── event_selection.py   # tz-aware next-event picker
│   │       └── leave_time.py        # compute_leave_decision
│   ├── expense_agent.py             # flat (legacy)
│   ├── calendar_agent.py            # flat (legacy)
│   ├── summary_agent.py             # flat (legacy)
│   ├── weather_agent.py             # flat (legacy)
│   ├── greeting_agent.py            # flat (legacy)
│   └── ambiguity_agent.py           # flat (legacy)
├── responder/
│   └── response_formatter.py        # Layer 4 — LLM → warm WhatsApp message
├── handlers/
│   ├── onboarding_handler.py        # Gate 1 — 5-state onboarding machine
│   ├── pending_expense_handler.py   # Gate 2 — currency follow-up
│   ├── pending_event_handler.py     # Gate 3 — calendar-clarify follow-up
│   └── pending_travel_handler.py    # Gate 4 — travel location + reminder 2-step machine
├── repositories/
│   ├── user_repository.py           # Firestore CRUD for users
│   ├── expense_repository.py        # Firestore CRUD for expenses
│   ├── unknown_message_repository.py # Write-only research log
│   └── scheduled_reminder_repository.py # create / list_due_within / delete
├── db/
│   └── user_context_store.py        # In-memory ephemeral context (not Firestore)
├── services/
│   ├── google_calendar.py           # Per-user Calendar API calls
│   ├── google_oauth.py              # build_authorize_url(), exchange_code()
│   ├── location_resolver.py         # Geocoding + Timezone resolution
│   ├── token_crypto.py              # Fernet encrypt/decrypt
│   ├── maps/maps_service.py         # Directions API
│   ├── weather/weather_service.py   # OpenWeatherMap
│   ├── morning_brief/               # Morning brief composer
│   └── whatsapp_sender.py           # WhatsApp Cloud API send
└── main.py                          # FastAPI app + APScheduler lifespan

# Dead files — do not extend:
app/routers/llm_intent_router.py     # DEAD — LLM routing violation
app/services/intent_classifier.py    # DEAD
app/services/expense_extractor.py    # DEAD
app/ai/ai_service.py                 # DEAD
app/services/response_service.py     # DEAD
```

---

## 14. Non-Negotiable Invariants

1. LLM only in Layer 1 (parse) and Layer 4 (format) — never in routing or agents
2. `signals` in ParsedMessage are always from deterministic keyword scan — never from LLM output
3. Router uses strict priority order — do not reorder without documented architectural reason
4. Travel checked before Calendar in router — "salir para mi reunión" must route to TravelAgent
5. All OAuth tokens Fernet-encrypted at rest — never store plaintext tokens in Firestore
6. OAuth state token is always opaque random — never the phone number
7. PKCE code_verifier must be stored at /authorize and passed at /callback — omitting causes invalid_grant
8. CalendarAgent always uses per-user refresh token — never the legacy global `get_today_events()`
9. Never ask user for currency during onboarding — deferred to first expense
10. `whatsapp_webhook.py` is a thin dispatcher — no business logic, no new feature branches inlined
11. New capability = new Agent in `/agents/` — always
12. User-facing errors are always warm human language — never technical strings, tracebacks, or JSON
13. `preferred_currency` is never overridden unless the user's message explicitly states a currency
14. Never commit directly to `main` or `develop` — always feature/* or hotfix/* branches
