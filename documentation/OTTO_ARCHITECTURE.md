# OTTO_ARCHITECTURE.md — System Architecture Overview
> High-level architecture reference for Otto (codename Alfred).
> Last updated: April 2026.
> For deep technical detail see `OTTO_ENGINEERING.md`. For the Agent/Skill pattern spec see `OTTO_AGENTS.md`.

---

## 1. What Otto is

Otto is a WhatsApp-native AI assistant. Users send natural language messages and receive warm, action-confirmed replies. It is **not a chatbot** — it is a deterministic action system with probabilistic language understanding.

**Core design axiom:** The LLM is a parser, not an orchestrator. It extracts structured data from natural language. A deterministic router and deterministic agents decide what to do with it.

---

## 2. The Full Request Lifecycle

Every inbound WhatsApp message travels through this pipeline in strict order:

```
WhatsApp Cloud API
        │
        ▼
┌─────────────────────────────────────────────┐
│  POST /webhook                              │
│  whatsapp_webhook.py (thin dispatcher)      │
│  - verify signature                         │
│  - normalize to InboundMessage              │
│  - load user from Firestore                 │
└──────────────┬──────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────┐
│  GATE 1 — Onboarding                        │
│  handlers/onboarding_handler.py             │
│  Returns True (consumed) or False (proceed) │
└──────────────┬──────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────┐
│  GATE 2 — Pending Expense                   │
│  handlers/pending_expense_handler.py        │
│  Catches currency follow-up replies         │
└──────────────┬──────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────┐
│  GATE 3 — Pending Event                     │
│  handlers/pending_event_handler.py          │
│  Catches calendar-clarify confirm/abort     │
└──────────────┬──────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────┐
│  GATE 4 — Pending Travel                    │
│  handlers/pending_travel_handler.py         │
│  Step 1: catches location reply             │
│  Step 2: catches reminder confirm/abort     │
└──────────────┬──────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────┐
│  LAYER 1 — Parser                           │
│  parser/message_parser.py                   │
│  GPT-4o-mini → ParsedMessage                │
│  signals always from deterministic scan     │
└──────────────┬──────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────┐
│  LAYER 2 — Router                           │
│  router/deterministic_router.py             │
│  No LLM. Returns Agent instance.            │
└──────────────┬──────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────┐
│  LAYER 3 — Agent                            │
│  agents/<domain>/                           │
│  Executes business logic via Skills         │
│  Returns AgentResult                        │
└──────────────┬──────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────┐
│  LAYER 4 — Responder                        │
│  responder/response_formatter.py            │
│  AgentResult → warm WhatsApp message        │
│  GPT-4o-mini for success cases              │
└──────────────┬──────────────────────────────┘
               │
               ▼
        WhatsApp Cloud API
        send_whatsapp_message()
```

**Gates run before the 4-layer pipeline.** Each gate returns `True` (message fully handled, stop here) or `False` (message is not for me, continue). A message consumed by a gate never reaches Layer 1.

---

## 3. The Four Layers

### Layer 1 — Parser
**File:** `app/parser/message_parser.py`
**Input:** raw text + user context (timezone, today's date)
**Output:** `ParsedMessage`

Calls GPT-4o-mini to extract structured fields: amount, currency, event title, event start time, etc. The LLM's only job is field extraction — it never classifies intent or decides actions.

`signals` (the routing keywords) are **always** from a deterministic keyword scan (`_scan_signals`), never from LLM output. This is the hard boundary between probabilistic and deterministic.

### Layer 2 — Router
**File:** `app/router/deterministic_router.py`
**Input:** `ParsedMessage`
**Output:** Agent instance

Pure logic, no I/O, no LLM. Reads `signals` and parsed fields, returns the correct Agent. Priority order is strict and documented — never reordered without an architectural reason.

```
1. REMINDER_TOGGLE → CalendarAgent    (settings must win everything)
2. amount present  → ExpenseAgent
3. TRAVEL_KEYWORDS → TravelAgent      (before Calendar — "salir para mi reunión")
4. WEATHER_KEYWORDS → WeatherAgent
5. SUMMARY_KEYWORDS → SummaryAgent    (before Calendar — "spent" beats "have")
6. CALENDAR_KEYWORDS → CalendarAgent
7. CREATE_KEYWORDS → CalendarAgent
8. event_reference → CalendarAgent
9. GREETING_KEYWORDS → GreetingAgent
10. GRATITUDE_KEYWORDS → GreetingAgent
11. else → AmbiguityAgent
```

### Layer 3 — Agents
**Directory:** `app/agents/`
**Input:** `ParsedMessage` + user dict
**Output:** `AgentResult(agent_name, success, data, error_message)`

Agents own their domain's business logic and Firestore reads/writes. They never call the LLM, never format user-facing text, and never know about WhatsApp.

There are two agent patterns (see Section 5):
- **Package pattern** (canonical): folder with a Skill registry — TravelAgent is the reference.
- **Flat pattern** (legacy): single file — all other agents until migrated.

### Layer 4 — Responder
**File:** `app/responder/response_formatter.py`
**Input:** `AgentResult` + user dict
**Output:** warm WhatsApp message string

Converts the agent's structured result into a natural language reply in the user's language (ES/EN). Uses GPT-4o-mini for success cases. Uses hardcoded copy for errors, opt-out confirmations, and other deterministic paths.

---

## 4. The Pre-Pipeline Gates

Gates exist because some capabilities are multi-turn: Otto says something, the user replies, and that reply needs to be routed to the right continuation — not re-parsed from scratch.

Each gate owns a stash in `user_context_store` (in-memory) or Firestore. It checks if the stash is set, and if so, intercepts the user's reply before it ever reaches Layer 1.

| Gate | Stash key | When set | What it catches |
|---|---|---|---|
| Gate 1 — Onboarding | `users/{phone}.onboarding_state` (Firestore) | New user | All messages until setup complete |
| Gate 2 — Pending Expense | `pending_expense` (in-memory) | `ExpenseAgent` returns `needs_currency=True` | Currency word reply |
| Gate 3 — Pending Event | `pending_event` (in-memory) | `CalendarAgent._handle_clarify_creation` | Yes/no/abort reply to event confirm |
| Gate 4 — Pending Travel | `pending_travel` (in-memory) | `NextEventTravelSkill` finds no location | Step 1: place name reply; Step 2: reminder confirm/abort |

**Gate design rules:**
- Each gate is single-responsibility — one domain, one flow.
- Gates call `Agent.run_skill(name, ctx)` directly — they bypass Layer 1 and Layer 2.
- A long or off-topic message always drops the stash and returns `False` so the pipeline runs normally.

---

## 5. The Agent/Skill Pattern (Layer 3 internals)

This is the architecture introduced with TravelAgent Phase 1. All new agents use this pattern.

```
Agent (package — app/agents/<domain>/)
├── execute(parsed, user) → AgentResult        ← Layer 2 entry point
│     │
│     └── _pick_skill(parsed, user) → skill_name   ← deterministic dispatch
│           │
│           └── _run(skill_name, ctx) → AgentResult
│                 │
│                 └── _SKILLS[skill_name]().execute(ctx) → SkillResult
│
└── run_skill(skill_name, ctx) → AgentResult   ← Gate entry point (bypasses Layer 1+2)
```

**Agent** = domain owner. Knows which Skill to call. Wraps `SkillResult → AgentResult`.

**Skill** = one capability. Takes `SkillContext`, returns `SkillResult`. Never calls LLM, never sends WhatsApp, never formats text.

**SkillContext** carries everything a Skill needs:
```python
SkillContext(
    user: dict,              # Firestore user doc
    inbound_text: str,       # raw message text
    parsed: ParsedMessage,   # None when called from a gate
    payload: dict,           # pending stash or other skill-specific state
)
```

**Why this structure:**
- Adding a new capability = adding one file (the new Skill) + one line (registry entry). Zero other files change.
- Skills are independently testable: they take context in, return result out, no hidden I/O.
- The Agent's public contract (`execute → AgentResult`) never changes regardless of how many Skills exist inside.
- At 40 agents × 10 skills = 400 files, each in its own folder, still readable by any engineer who knows the pattern.

---

## 6. The Autonomous Cron (every 15 minutes)

Otto does several things proactively without any user trigger, all in a single 15-minute cron cycle.

```
APScheduler (in-process, app/main.py)
    │
    └── run_cron_job()   [app/api/cron_routes.py]
          │
          ├── Pass 1: OAuth follow-ups
          │     Re-mint fresh state token → re-send calendar connect link
          │     (users who haven't connected calendar yet)
          │
          ├── Pass 2: Location retries
          │     Re-run geocoding for users with pending_retry status
          │
          ├── Pass 3: 1-hour event reminders
          │     Fetch events starting in 55–75 min → send reminder
          │     Dedup via notified_event_ids {eventId}:{local_date}
          │
          ├── Pass 4: Morning brief
          │     Local time 06:00–06:14 → send agenda + weather + travel to first event
          │     Dedup via morning_brief_sent_date
          │
          └── Pass 5: Departure reminders
                Query scheduled_reminders where fire_at ∈ [now−5min, now+15min]
                → send → delete doc (no accumulation)
```

Departure reminders are persisted to Firestore so they survive Railway restarts between scheduling and delivery.

---

## 7. Data Architecture

### Ephemeral context (in-memory)
`app/db/user_context_store.py` — Python dict, lives in the process. Used by gates for short-lived follow-up state. Resets on restart — intentional. Never used for durable state.

### Durable state (Firestore)
All persistent data is in Firestore. No SQL anywhere.

| Collection | Purpose | TTL |
|---|---|---|
| `users` | User profile, OAuth tokens, preferences, dedup keys | Permanent |
| `expenses` | Expense records | Permanent |
| `scheduled_reminders` | Pending departure reminders | Deleted on delivery |
| `unknown_messages` | Research log — unrecognized messages | No TTL (analytics) |
| `user_context` | Exists but unused — reserved | — |

### Token security
Google Calendar refresh tokens are Fernet-encrypted before writing to Firestore. The key lives in `CALENDAR_TOKEN_ENCRYPTION_KEY` env var — generated once, never rotated.

---

## 8. Where Does New Code Go?

Use this decision tree when adding something new:

```
New user-facing capability?
├── In an existing domain (travel, calendar, expenses…)?
│   └── Add a new Skill to that Agent.
│       (new file in skills/, one line in _SKILLS registry)
│
└── In a new domain?
    └── Create a new Agent package.
        (follow the checklist in OTTO_AGENTS.md § Adding a New Agent)

New multi-turn interaction (Otto asks → user replies)?
└── Add a step to the domain's pending_handler gate.
    (or create handlers/pending_<domain>_handler.py if new)

New automatic background task?
└── Add a cron pass in cron_routes.py.
    Add a repository in repositories/ if new Firestore collection needed.

New shared utility used by multiple agents?
└── Add to app/services/ (not inside any agent's _shared/).

New user-facing copy (error messages, confirmations)?
└── Add to response_formatter.py:
    - Hardcoded copy → _SPECIFIC_ERRORS or a short-circuit block
    - LLM-formatted → FORMATTING_PROMPT (use [variable] not {variable})
```

**Never:**
- Put business logic in `whatsapp_webhook.py`
- Put routing decisions in an Agent or Skill
- Call the LLM from a router, agent, or skill
- Add structured commands (e.g. `/remind`) — always natural language

---

## 9. Document Map

| Document | What it covers |
|---|---|
| `OTTO_ARCHITECTURE.md` (this file) | System overview — start here |
| `OTTO_ENGINEERING.md` | Deep technical reference: all contracts, schemas, security, infra |
| `OTTO_AGENTS.md` | Agent/Skill pattern spec and checklists |
| `OTTO_CAPABILITIES.md` | What Otto can do today, listed by capability |
| `CLAUDE.md` (repo root) | Coding instructions and hard rules for Claude Code |
