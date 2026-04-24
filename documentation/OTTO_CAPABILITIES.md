# OTTO_CAPABILITIES.md
> What Otto can actually do today.
> Last updated: April 2026 — beta launch + TravelAgent Phase 1 + ListAgent
> Companion to `OTTO_AGENTS.md` (architecture) and `OTTO_ENGINEERING.md` (technical reference).

---

## How to read this doc

Each capability is one thing a user can get Otto to do (or Otto does for them automatically).

- **Agent** — which Agent owns it.
- **Pattern** — `flat` (one file in `app/agents/`, legacy) or `package` (folder with skills, per `OTTO_AGENTS.md`).
- **Trigger** — user-initiated, gate-driven (after Otto asked something), or automatic (cron).

When an Agent migrates from flat to package, update its rows here. When a new capability ships, add a row.

---

## 💬 Greetings

| Capability | Agent | Pattern | Trigger |
|---|---|---|---|
| Respond to hellos, good mornings, good evenings in ES/EN | GreetingAgent | flat | user |

---

## 💸 Expenses

| Capability | Agent | Pattern | Trigger |
|---|---|---|---|
| Log an expense from natural language ("Pagué 50 mil en el almuerzo") | ExpenseAgent | flat | user |
| Ask for currency if ambiguous, then lock it as `preferred_currency` | ExpenseAgent + `pending_expense_handler` | flat + gate | gate-driven |
| Handle amounts written as words ("dos millones", "50 mil") | Parser (Layer 1) | — | user |

---

## 📊 Expense Summary

| Capability | Agent | Pattern | Trigger |
|---|---|---|---|
| Summarize spending by period (today, this week, last week, this month, last month, this year) | SummaryAgent | flat | user |
| Break down by currency when user has mixed currencies | SummaryAgent | flat | user |

---

## 📅 Calendar

| Capability | Agent | Pattern | Trigger |
|---|---|---|---|
| Query today's agenda / upcoming events | CalendarAgent | flat | user |
| Create a new event from natural language | CalendarAgent | flat | user |
| Clarify creation when fields are missing (two-step confirm) | CalendarAgent + `pending_event_handler` | flat + gate | gate-driven |
| Follow up on a referenced event ("the second one", "that meeting") | CalendarAgent | flat | user |
| Toggle 1-hour event reminders on/off | CalendarAgent | flat | user |
| Send 1-hour event reminders | cron (`cron_routes.py`) | — | automatic |

---

## 🌤 Weather

| Capability | Agent | Pattern | Trigger |
|---|---|---|---|
| Current conditions for the user's saved city | WeatherAgent | flat | user |
| Current conditions for a different named city | WeatherAgent | flat | user |

---

## 🚗 Travel

| Capability | Agent | Pattern | Trigger |
|---|---|---|---|
| Compute leave time for next calendar event with real Google Maps traffic | TravelAgent | package | user |
| When an event has no location, ask the user for it | TravelAgent | package | user |
| Accept a free-form place name, geocode it, compute leave time | TravelAgent + `pending_travel_handler` | package + gate | gate-driven |
| Offer and schedule a one-off departure reminder (Firestore-persisted) | TravelAgent + `pending_travel_handler` | package + gate | gate-driven |
| Deliver the departure reminder at the right moment | cron (`cron_routes.py`) | — | automatic |
| Abort gracefully at any step ("no", "cancel", "déjalo") | TravelAgent + `pending_travel_handler` | package + gate | gate-driven |

---

## 🌅 Morning Brief (automatic)

| Capability | Agent | Pattern | Trigger |
|---|---|---|---|
| Send at 6:00 AM in user's local timezone | `services/morning_brief/` + cron | — | automatic |
| Full day agenda (event count + first event) | `services/morning_brief/` | — | automatic |
| Current weather for user's city | `services/morning_brief/` | — | automatic |
| Leave time + traffic estimate for first event with location | `services/morning_brief/` | — | automatic |
| Deliver once per day (skip if already sent) | cron dedup via `morning_brief_sent_date` | — | automatic |

---

## 📋 Lists

User-defined named lists for save / recall / delete. Triggered by natural language — no commands. 3 lists per user max, 10-minute retry-loop dedup inside saves.

| Capability | Agent | Pattern | Trigger |
|---|---|---|---|
| Save a link or note to a list via natural language ("guarda este link en mi lista de libros") | ListAgent | package | user |
| Auto-create `"guardados"` (ES) / `"saved"` (EN) when a user with zero lists saves without naming one | ListAgent | package | user |
| Save to the only existing list when no name is given | ListAgent | package | user |
| Ask which list when the user has 2–3 lists and didn't name one | ListAgent + `pending_list_handler` | package + gate | gate-driven |
| Silently suppress an identical save within 10 minutes (retry-loop dedup) | ListAgent | package | user |
| Block the 4th list creation, naming the existing three so the user can delete one | ListAgent | package | user |
| Recall the items of a named list (case-insensitive); auto-pick the single list when unambiguous | ListAgent | package | user |
| Generate 3–6 word descriptions for URL items on recall (batched, fail-open) | ListAgent + Layer 4 LLM | package | user |
| Report empty list or missing list warmly, surfacing the user's existing names | ListAgent | package | user |
| Stage a delete with explicit confirmation — destructive ops never auto-pick | ListAgent + `pending_list_handler` | package + gate | gate-driven |
| Execute the delete after the user confirms ("sí"/"yes") | ListAgent + `pending_list_handler` | package + gate | gate-driven |
| Disambiguate when a message could be a list op or another action ("guarda 50.000 de almuerzo") | router + `pending_list_handler` | package + gate | gate-driven |
| Abort any list flow gracefully ("no", "cancela", "nvm") | ListAgent + `pending_list_handler` | package + gate | gate-driven |

---

## 🤷 Ambiguity / Out of scope

| Capability | Agent | Pattern | Trigger |
|---|---|---|---|
| Detect out-of-scope requests and respond warmly | AmbiguityAgent | flat | user |
| Log true ambiguity and capability requests to `unknown_messages` | AmbiguityAgent | flat | user |

---

## 🔐 Onboarding (system, not user-invoked)

| Capability | Handler | Pattern | Trigger |
|---|---|---|---|
| Language detection (ES/EN) on first contact | `onboarding_handler` | — | automatic |
| Name + city extraction | `onboarding_handler` + `name_city_extractor` | — | gate-driven |
| Location resolution via Geocoding + Timezone | `services/location_resolver` | — | automatic |
| Google Calendar OAuth connection | `oauth_routes` + `onboarding_handler` | — | user |
| Beta gate confirmation | `onboarding_handler` | — | gate-driven |

---

## Migration snapshot

| Agent | Pattern | Migrate when |
|---|---|---|
| TravelAgent | package | — (reference) |
| ListAgent | package | — (new, package from day one) |
| ExpenseAgent | flat | next new Skill |
| CalendarAgent | flat | next new Skill |
| SummaryAgent | flat | next new Skill |
| WeatherAgent | flat | next new Skill |
| GreetingAgent | flat | no planned growth |
| AmbiguityAgent | flat | next new Skill |

Migration rule (from `OTTO_AGENTS.md`): **an Agent migrates the next time it gains a new capability, never speculatively.**

---

## Known gaps (not yet capabilities)

- No proactive suggestions (Otto is reactive except for the morning brief and event reminders).
- No budget goals or spending alerts.
- No recurring expense tracking.
- No natural language event editing or deletion.
- WhatsApp only — no Telegram, Slack, Discord yet.

See `OTTO_PRODUCT.md` roadmap for when these are planned.
