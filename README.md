# Otto — WhatsApp AI Personal Assistant

Otto is a WhatsApp-based AI assistant that understands natural language. Users text naturally — *"Pague dos millones en arriendo"*, *"How is my day today?"*, *"A qué hora debo salir para mi reunión?"* — and Otto handles expenses, calendars, weather, travel time, and more.

**Design principle:** Otto is not a chatbot. It is a deterministic system with probabilistic understanding — the LLM parses intent, a rule-based router decides actions.

---

## Features

- **Expense tracking** — log expenses in any currency with natural language ("200 mil pesos en comida")
- **Google Calendar** — query today's events, follow-up on specific events by ordinal
- **Travel time** — "When should I leave for my meeting?" → Maps API leave-time calculation
- **Weather** — current conditions for the user's city or any city mentioned in the message
- **Expense summaries** — spending by date range and currency
- **Multilingual** — full English and Spanish support, detected at onboarding
- **Onboarding** — 5-state machine: language → profile → location → Google Calendar OAuth → done

---

## Architecture

Requests flow through 4 strict layers. Each layer has a single responsibility and defined contracts.

```
WhatsApp POST /webhook
  → Onboarding gate        (handlers/onboarding_handler.py)
  → Pending expense gate   (handlers/pending_expense_handler.py)
  → Parser     [Layer 1]   (parser/message_parser.py)        raw text → ParsedMessage
  → Router     [Layer 2]   (router/deterministic_router.py)  ParsedMessage → Agent
  → Agent      [Layer 3]   (agents/*.py)                     business logic → AgentResult
  → Responder  [Layer 4]   (responder/response_formatter.py) AgentResult → WhatsApp message
```

| Layer | Rule |
|---|---|
| Parser | LLM only. Extracts amount, currency, category hint, date hint. Never classifies intent. |
| Router | Pure keyword logic. No LLM, no Firestore. Priority: Expense → Travel → Weather → Summary → Calendar. |
| Agents | Business logic only. No LLM, no WhatsApp formatting. |
| Responder | LLM only. Formats the warm, language-aware reply. No routing, no Firestore. |

---

## Tech Stack

| Component | Technology |
|---|---|
| Runtime | Python 3.13 |
| Web framework | FastAPI + Uvicorn |
| Database | Google Firestore |
| Messaging | WhatsApp Cloud API |
| LLM | OpenAI GPT-4o-mini |
| Calendar | Google Calendar API (per-user OAuth 2.0 + PKCE) |
| Maps | Google Maps Directions + Geocoding + Timezone APIs |
| Weather | OpenWeatherMap API |
| Scheduling | APScheduler (in-process, AsyncIOScheduler) |
| Deployment | Railway (NIXPACKS) |

---

## Getting Started

### Prerequisites

- Python 3.13
- A WhatsApp Cloud API app (Meta Developer Console)
- A Google Cloud project with Calendar, Maps Directions, Geocoding, and Timezone APIs enabled
- A Firebase project with Firestore enabled
- An OpenAI API key
- An OpenWeatherMap API key

### Local setup

```bash
git clone <repo>
cd alfred-backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in your values
uvicorn app.main:app --host 0.0.0.0 --port 8001 --reload
```

### Environment variables

Create a `.env` file at the project root:

```env
# WhatsApp
WHATSAPP_ACCESS_TOKEN=
WHATSAPP_PHONE_NUMBER_ID=
WHATSAPP_VERIFY_TOKEN=

# Firebase
# Local: use file path
FIREBASE_CREDENTIALS_PATH=credentials/firebase-service-account.json
# Production (Railway): paste the raw JSON content of your service account file
# FIREBASE_CREDENTIALS_JSON={"type":"service_account","project_id":...}

# OpenAI
OPENAI_API_KEY=

# Google APIs (requires Directions + Geocoding + Timezone enabled in GCP)
GOOGLE_MAPS_API_KEY=

# Weather
OPENWEATHER_API_KEY=

# Google Calendar OAuth
# Create a Web OAuth 2.0 client in GCP Console → APIs & Services → Credentials
GOOGLE_OAUTH_CLIENT_ID=
GOOGLE_OAUTH_CLIENT_SECRET=
GOOGLE_OAUTH_REDIRECT_URI=https://<your-domain>/auth/google/callback
PUBLIC_BASE_URL=https://<your-domain>

# Fernet key for encrypting per-user Calendar refresh tokens
# Generate once: python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# Never rotate this without migrating existing tokens first.
CALENDAR_TOKEN_ENCRYPTION_KEY=

# Cron endpoint protection
CRON_SHARED_SECRET=

ENVIRONMENT=development
```

---

## Project Structure

```
app/
├── api/
│   ├── whatsapp_webhook.py        # Webhook verify + receive. Thin dispatcher only.
│   ├── oauth_routes.py            # GET /auth/google/authorize|callback|done
│   └── cron_routes.py             # POST /cron/oauth-followups (secret-protected)
├── parser/
│   ├── message_parser.py          # Layer 1: LLM → ParsedMessage
│   ├── word_number_parser.py      # "dos millones" → 2000000, "50 mil" → 50000
│   └── name_city_extractor.py    # Onboarding: extract name + city from free text
├── router/
│   └── deterministic_router.py   # Layer 2: keyword routing, no LLM
├── agents/
│   ├── base_agent.py
│   ├── expense_agent.py
│   ├── calendar_agent.py          # Uses per-user OAuth token — never the global path
│   ├── travel_agent.py            # Uses per-user OAuth token — never the global path
│   ├── summary_agent.py
│   ├── weather_agent.py
│   └── ambiguity_agent.py
├── responder/
│   └── response_formatter.py     # Layer 4: LLM → warm WhatsApp reply
├── handlers/
│   ├── onboarding_handler.py     # 5-state onboarding machine (pre-pipeline gate)
│   ├── onboarding_copy.py        # Bilingual static strings — no LLM in onboarding
│   └── pending_expense_handler.py
├── repositories/
│   ├── expense_repository.py
│   ├── user_repository.py
│   └── unknown_message_repository.py  # Product research log (ambiguous messages)
├── services/
│   ├── google_calendar.py         # Calendar API helpers (always use per-user functions)
│   ├── google_oauth.py            # PKCE OAuth flow: build_authorize_url, exchange_code
│   ├── location_resolver.py       # Geocoding + Timezone resolution
│   ├── token_crypto.py            # Fernet encrypt/decrypt for refresh tokens
│   ├── maps/maps_service.py
│   ├── weather/weather_service.py
│   ├── morning_brief/             # Scheduled morning summary (separate feature)
│   ├── message_router.py
│   ├── inbound_message_mapper.py
│   └── whatsapp_sender.py
├── db/
│   ├── user_context_store.py      # In-memory context (ephemeral — resets on restart)
│   └── firestore_context_store.py # Firestore-backed context (exists, not used by agents)
├── models/
│   ├── parsed_message.py
│   ├── agent_result.py
│   ├── extracted_expense.py
│   ├── inbound_message.py
│   └── webhook_event.py
├── core/
│   └── firebase.py                # Firestore client — reads FIREBASE_CREDENTIALS_JSON on Railway
└── main.py                        # FastAPI app + APScheduler lifespan
```

---

## Key Design Decisions

### Google Calendar OAuth (PKCE)

Each user connects their own Google Calendar through a web OAuth flow. The OAuth library (`google-auth-oauthlib`) uses PKCE by default — it generates a `code_verifier` during the authorization step and Google requires it back during the token exchange.

Because authorization and callback are separate HTTP requests (with separate Flow objects), the `code_verifier` is stored in Firestore at `/auth/google/authorize` and retrieved at `/auth/google/callback`. Skipping this causes `(invalid_grant) Missing code verifier` from Google.

Per-user refresh tokens are Fernet-encrypted before storing. The encryption key (`CALENDAR_TOKEN_ENCRYPTION_KEY`) must be generated once and never rotated without a data migration.

### In-memory context store

`CalendarAgent` and `TravelAgent` store today's events and the last referenced event in an in-memory dict (`user_context_store.py`). This supports short-lived conversational follow-ups ("and the second one?") without Firestore round-trips. Context resets on server restart, which is acceptable — follow-ups only make sense within the same session.

### Expense currency deferral

Users are never asked for currency during onboarding. On the first expense, if the message contains an explicit currency word ("pesos", "dolares", "$"), it's silently locked in as `preferred_currency`. Otherwise, `ExpenseAgent` returns `needs_currency=True` and asks once. Currency is never overridden without an explicit user signal.

### APScheduler (in-process cron)

The OAuth follow-up cron runs inside the FastAPI process via `AsyncIOScheduler` — no external cron service needed on Railway. `run_cron_job` is synchronous. If future cron work grows significantly, move it to `asyncio.get_event_loop().run_in_executor()` to avoid blocking the event loop.

---

## Deployment (Railway)

**Live URL:** `https://alfredproject-otto-life-co-pilot-production.up.railway.app`

The app deploys automatically on push to `main` via the `railway.json` config.

**Critical configuration:**
- Builder: **NIXPACKS** (not RAILPACK)
- Start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
- `--host 0.0.0.0` is mandatory. Binding to `127.0.0.1` causes 502s.
- Do NOT set `PORT` manually in Railway Variables — Railway injects it automatically.
- In Railway Settings → Networking, the domain port must match the port the app binds to. A mismatch causes `connection refused` on all external requests while internal healthchecks still pass.

**Firebase on Railway:** Set `FIREBASE_CREDENTIALS_JSON` to the raw JSON content of your `firebase-service-account.json` file. The app writes it to a temp file at startup.

**WhatsApp webhook:** After deploying, go to Meta Developer Console → WhatsApp → Configuration:
- Callback URL: `https://<your-railway-domain>/webhook`
- Verify token: value of `WHATSAPP_VERIFY_TOKEN`

---

## Firestore Schema

### `users` (doc ID = phone number e.g. `+573001234567`)

| Field | Type | Description |
|---|---|---|
| `name` | string | User's first name |
| `language` | string | `"es"` or `"en"` |
| `preferred_currency` | string | `"COP"`, `"USD"`, `"EUR"` — set on first expense |
| `timezone` | string | IANA tz (e.g. `"America/Bogota"`) |
| `location` | string | Normalized city from Geocoding API |
| `latitude`, `longitude` | number | Coordinates |
| `onboarding_state` | string | `language_pending` → `profile_pending` → `oauth_pending` → `completed` |
| `google_calendar_refresh_token` | string | Fernet-encrypted OAuth refresh token |
| `google_oauth_state_token` | string | One-time PKCE state token (cleared after callback) |
| `google_oauth_code_verifier` | string | PKCE verifier (cleared after callback) |
| `google_oauth_state_expires_at` | timestamp | 1h expiry on the state token |

### `expenses` (auto ID)

| Field | Type | Description |
|---|---|---|
| `user_phone_number` | string | |
| `amount` | number | |
| `currency` | string | `"COP"`, `"USD"`, `"EUR"` |
| `category` | string | `food`, `transport`, `shopping`, `health`, `other` |
| `user_message` | string | Original raw message |
| `created_at` | timestamp | |

### `unknown_messages` (auto ID)

Logs every message routed to `AmbiguityAgent` or that failed to process. Raw message is never filtered. Used for product iteration.

---

## API Endpoints

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/` | None | Health check |
| `GET` | `/webhook` | WhatsApp verify token | Webhook verification |
| `POST` | `/webhook` | WhatsApp signature | Incoming messages |
| `GET` | `/auth/google/authorize` | State token | Start Calendar OAuth |
| `GET` | `/auth/google/callback` | Google code + state | OAuth callback |
| `GET` | `/auth/done` | None | Post-OAuth success page |
| `POST` | `/cron/oauth-followups` | `X-Cron-Secret` header | Trigger cron job |

---

## Adding a New Feature

1. Create `app/agents/your_agent.py` extending `BaseAgent`
2. Add a keyword set in `deterministic_router.py` and mirror it in `message_parser.py`
3. Add a routing rule in `deterministic_router.py` (respect priority order — see CLAUDE.md)
4. Add `_FALLBACKS` and `_ERROR_MESSAGES` entries in `response_formatter.py`
5. Add agent-specific formatting instructions in `FORMATTING_PROMPT`
6. Do not touch `whatsapp_webhook.py`
