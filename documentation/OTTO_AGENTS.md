# OTTO_AGENTS.md — Agent/Skill Pattern
> Canonical spec for Otto's Agent/Skill architecture.
> Last updated: April 2026 — TravelAgent Phase 1 (reference implementation).
> Every new Agent and every new Skill follows this document.

---

## 1. What an Agent is

An Agent is a **self-contained package** that owns one product domain (travel, calendar, finance, …).
It is the only public interface the rest of the system uses for that domain.

- Lives at `app/agents/<domain_name>/`
- Public contract: `execute(parsed: ParsedMessage, user: dict) → AgentResult`
- Gate contract: `run_skill(skill_name: str, ctx: SkillContext) → AgentResult`
- Nothing inside the package leaks outward. Other modules import the class, not its internals.

## 2. What a Skill is

A Skill is the **smallest coherent capability** inside an Agent's domain.
One Skill = one thing Otto can actually do.

- Lives at `app/agents/<domain_name>/skills/<skill_name>.py`
- Inherits from the Agent's skill ABC (e.g. `TravelSkill`)
- Method signature: `execute(ctx: SkillContext) → SkillResult`
- **No LLM calls.** Skills are deterministic executors.
- **No WhatsApp calls.** Skills never call `send_whatsapp_message`.
- **No user-facing string composition.** That belongs in Layer 4 (Responder).
- **No routing decisions.** The Agent already picked this skill.
- External APIs (Maps, Calendar, Geocoding) are allowed — they are the domain's tools.

---

## 3. Contracts

```python
# app/agents/<domain>/skill_context.py

@dataclass(frozen=True)
class SkillContext:
    user: dict                          # full user Firestore doc
    inbound_text: str                   # raw user text
    parsed: Optional[ParsedMessage]     # None when a gate calls run_skill directly
    payload: dict = field(default_factory=dict)  # skill-specific state (e.g. pending stash)

@dataclass
class SkillResult:
    success: bool
    data: dict = field(default_factory=dict)   # passed through to AgentResult.data
    error_message: Optional[str] = None        # passed through to AgentResult.error_message
```

The Agent wraps `SkillResult → AgentResult` in one place (`_run`). Skills never construct `AgentResult`.

---

## 4. Folder structure

```
app/agents/<domain_name>/
├── __init__.py              # re-exports the Agent class only
├── agent.py                 # Agent class: _SKILLS registry, execute, run_skill, _run
├── skill_context.py         # SkillContext (frozen dataclass) + SkillResult
├── skills/
│   ├── __init__.py
│   ├── base.py              # Domain skill ABC (<Domain>Skill)
│   └── <skill_name>.py      # one file per skill
└── _shared/
    ├── __init__.py
    └── <helper>.py          # helpers used by ≥2 skills in this agent ONLY
```

`_shared/` is private to the agent. If a helper is needed across agents, it graduates to `app/services/`.

---

## 5. How an Agent dispatches (deterministic)

```python
class MyAgent(BaseAgent):
    agent_name = "MyAgent"

    _SKILLS = {
        "skill_a": SkillA,
        "skill_b": SkillB,
    }

    def execute(self, parsed: ParsedMessage, user: dict) -> AgentResult:
        skill_name = self._pick_skill(parsed, user)   # deterministic, no LLM
        ctx = SkillContext(user=user, parsed=parsed, inbound_text=parsed.raw_message)
        return self._run(skill_name, ctx)

    def run_skill(self, skill_name: str, ctx: SkillContext) -> AgentResult:
        return self._run(skill_name, ctx)             # used by gates

    def _pick_skill(self, parsed, user) -> str:
        # Condition-based. No LLM. Same discipline as Layer 2.
        return "skill_a"

    def _run(self, skill_name: str, ctx: SkillContext) -> AgentResult:
        skill_cls = self._SKILLS.get(skill_name)
        if skill_cls is None:
            return AgentResult(agent_name=self.agent_name, success=False,
                               error_message=f"unknown_skill:{skill_name}")
        try:
            result = skill_cls().execute(ctx)
        except Exception as exc:
            return AgentResult(agent_name=self.agent_name, success=False,
                               error_message=str(exc))
        return AgentResult(agent_name=self.agent_name, success=result.success,
                           data=result.data, error_message=result.error_message)
```

### When dispatch grows complex

Add a `matches` classmethod to each skill and iterate in `_pick_skill`:

```python
class SkillA(MySkillBase):
    @classmethod
    def matches(cls, parsed: ParsedMessage, user: dict) -> bool:
        return "keyword" in parsed.signals

# In _pick_skill:
return next((n for n, cls in self._SKILLS.items() if cls.matches(parsed, user)), "default_skill")
```

---

## 6. Skill vs _shared helper — the test

**It's a Skill if** it represents a user-invocable capability with its own dispatch condition.

**It's a `_shared` helper if** it is infrastructure used by Skills (external API client, math, data transformation). It has no user-facing semantic on its own and is never addressed from outside the agent.

**Example:** `schedule_departure_reminder` is a Skill — the user explicitly confirms "sí" to invoke it. `leave_time.compute_leave_decision` is a `_shared` helper — it's arithmetic called by multiple skills with no user interaction.

---

## 7. Integration with the 4-layer pipeline

```
WhatsApp POST /webhook
  → onboarding gate           [handlers/onboarding_handler.py]       Gate 1
  → pending_expense gate      [handlers/pending_expense_handler.py]   Gate 2
  → pending_event gate        [handlers/pending_event_handler.py]     Gate 3
  → pending_<domain> gate     [handlers/pending_<domain>_handler.py]  Gate N (one per stateful agent)
  → parse_message()           Layer 1  [parser/message_parser.py]
  → route(parsed)             Layer 2  [router/deterministic_router.py]
  → agent.execute(parsed,user)Layer 3  [agents/<domain>/agent.py]
  → format_response(result)   Layer 4  [responder/response_formatter.py]
  → send_whatsapp_message()
```

**Invariants that must never break:**
- `execute(parsed, user) → AgentResult` is the only contract Layer 2 and Layer 4 know about.
- Skills are invisible to everything outside the agent package.
- Gates are thin: detect pending state → `agent.run_skill(name, ctx)` → `format_response` → send. No domain logic in the gate itself.
- `whatsapp_webhook.py` stays thin. All pre-pipeline multi-turn state → own `handlers/*.py` gate.

---

## 8. TravelAgent — reference implementation

`app/agents/travel_agent/` is the canonical example. Read this first when building a new agent.

| File | Purpose |
|---|---|
| `__init__.py` | Re-exports `TravelAgent`. Import site unchanged when file → package. |
| `agent.py` | `_SKILLS` dict registry. `execute` → `_pick_skill_from_router` → `_run`. `run_skill` for gate entry. |
| `skill_context.py` | `SkillContext` (frozen) + `SkillResult`. |
| `skills/base.py` | `TravelSkill` ABC. |
| `skills/next_event_travel.py` | Computes leave time for next calendar event. Stashes `pending_travel` when no location. |
| `skills/resolve_event_location.py` | Geocodes a user-supplied place name, computes leave time. |
| `skills/schedule_departure_reminder.py` | Persists a one-off departure reminder to `scheduled_reminders`. |
| `_shared/event_selection.py` | Tz-aware next-upcoming-event picker (shared by multiple skills). |
| `_shared/leave_time.py` | `compute_leave_decision` helper. |

Gate: `app/handlers/pending_travel_handler.py` — two-step state machine (`awaiting_location` / `awaiting_reminder_confirmation`).

Repository: `app/repositories/scheduled_reminder_repository.py` — `create`, `list_due_within`, `delete`.

Cron delivery: `_run_departure_reminders()` in `app/api/cron_routes.py` (5th cron pass, 15-min cadence). Docs deleted after delivery.

---

## 9. Scaling notes (flagged for ~10+ agents)

1. **Layer 1 keyword sprawl** — move per-domain keyword sets into the agent package; `_scan_signals` composes the union from a registry. Removes parser edits per new agent.
2. **Layer 2 router branching** — same registry approach: each agent declares its routing predicate; the router iterates. Removes router edits per new agent.
3. **Agent folder grouping** — if `app/agents/` grows beyond ~15 packages, group by product area (`app/agents/productivity/`, `app/agents/finance/`). Safe rename since agents are self-contained.

---

## 10. Adding a New Agent (checklist)

1. Create `app/agents/<domain>/` package following the folder structure in Section 4.
2. Define `SkillContext` and `SkillResult` in `skill_context.py` (copy from TravelAgent).
3. Define the skill ABC in `skills/base.py`.
4. Implement at least one skill in `skills/<name>.py`. Enforce the no-LLM, no-WhatsApp, no-formatting rules.
5. Register skills in `_SKILLS` in `agent.py`. Implement `_pick_skill` deterministically.
6. Export the Agent class from `__init__.py`.
7. Add import + routing rule in `router/deterministic_router.py` (respect priority order; Travel before Calendar is a hard rule).
8. Add keyword set to `parser/message_parser.py` (`_scan_signals`) if the domain has unique trigger words.
9. Add `_FALLBACKS` and `_ERROR_MESSAGES` entries in `responder/response_formatter.py`.
10. Add agent-specific formatting instructions in `FORMATTING_PROMPT` (use `[variable]` not `{variable}`).
11. If the agent has multi-turn state, create `handlers/pending_<domain>_handler.py` and register it in `whatsapp_webhook.py` (after existing gates, before the pipeline).
12. If the agent schedules async work, add a cron pass in `cron_routes.py` + a repository in `repositories/`.
13. Update `CLAUDE.md` (Firestore schema, file structure, Hard Rules if new invariants).
14. Update `documentation/OTTO_ENGINEERING.md` (agent table, cron section, file tree).
15. Never touch `whatsapp_webhook.py` beyond gate registration.

## 11. Adding a New Skill to an Existing Agent (checklist)

1. Confirm it's a Skill, not a `_shared` helper (Section 6 test).
2. Create `app/agents/<domain>/skills/<skill_name>.py` implementing the domain ABC.
3. Register it in `_SKILLS` in `agent.py` (one line).
4. Add dispatch condition in `_pick_skill` — deterministic only.
5. If the skill needs a new gate step: extend the pending handler's `step` state machine.
6. If the skill has a new success `type`: add LLM prompt instructions or a hardcoded short-circuit in `format_response`.
7. If new failure modes: add to `_SPECIFIC_ERRORS` in the responder.
8. No changes to the router or parser required unless the skill has new trigger keywords.

---

## 12. Migration status (April 2026)

| Agent | Pattern | Migrate when |
|---|---|---|
| TravelAgent | **package (reference)** | — already migrated |
| ExpenseAgent | flat (legacy) | next new Skill |
| CalendarAgent | flat (legacy) | next new Skill |
| SummaryAgent | flat (legacy) | next new Skill |
| WeatherAgent | flat (legacy) | next new Skill |
| GreetingAgent | flat (legacy) | no planned growth |
| AmbiguityAgent | flat (legacy) | next new Skill |

**Migration rule: an Agent migrates the next time it gains a new Skill. Never speculatively.**
