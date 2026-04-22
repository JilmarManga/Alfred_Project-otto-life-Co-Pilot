# OTTO_AGENTS.md — Agent/Skill Pattern

Single source of truth for how Agents and Skills are built in Otto.
Every new Agent from this point forward must follow this spec.
Existing flat agents (Expense, Calendar, Summary, Weather, Greeting, Ambiguity) migrate one-by-one in future tasks.

---

## What an Agent is

An Agent is a **self-contained package** that owns a product domain (travel, calendar, finance, …).
It is the only public interface the rest of the system uses for that domain.

- Lives at `app/agents/<domain_name>/`
- Public contract: `execute(parsed: ParsedMessage, user: dict) → AgentResult`
- Optional gate contract: `run_skill(skill_name: str, ctx: SkillContext) → AgentResult`
- Nothing inside the package leaks outward. Other modules import the class, not its internals.

## What a Skill is

A Skill is the **smallest coherent capability** inside an Agent's domain.
One skill = one thing Otto can actually do.

- Lives at `app/agents/<domain_name>/skills/<skill_name>.py`
- Inherits from `TravelSkill` (or the domain's equivalent base class)
- Method signature: `execute(ctx: SkillContext) → SkillResult`
- **No LLM calls.** Skills are deterministic executors.
- **No WhatsApp calls.** Skills do not call `send_whatsapp_message`.
- **No user-facing string composition.** That belongs in Layer 4 (Responder).
- **No routing decisions.** The Agent already picked this skill.
- External APIs (Maps, Calendar, Geocoding) are allowed — they are the domain's tools.

---

## Contracts

```python
# app/agents/<domain>/skill_context.py

@dataclass(frozen=True)
class SkillContext:
    user: dict                          # full user Firestore doc
    inbound_text: str                   # raw user text
    parsed: Optional[ParsedMessage]     # None when a gate calls run_skill directly
    payload: dict                       # skill-specific state (e.g. the pending stash)

@dataclass
class SkillResult:
    success: bool
    data: dict                          # passed through to AgentResult.data
    error_message: Optional[str]        # passed through to AgentResult.error_message
```

The Agent wraps `SkillResult → AgentResult` in one place (`_run`). Skills never construct `AgentResult`.

---

## Folder structure convention

```
app/agents/<domain_name>/
├── __init__.py              # re-exports the Agent class only
├── agent.py                 # Agent class: _SKILLS registry, execute, run_skill, _run
├── skill_context.py         # SkillContext (frozen dataclass) + SkillResult
├── skills/
│   ├── __init__.py
│   ├── base.py              # Domain skill ABC (TravelSkill, etc.)
│   └── <skill_name>.py      # one file per skill
└── _shared/
    ├── __init__.py
    └── <helper>.py          # helpers used by ≥2 skills in this agent ONLY
```

`_shared/` is private to the agent (single underscore = not for external import).
If a helper is genuinely needed across agents, it graduates to `app/services/`.

---

## How an Agent dispatches to Skills (deterministic)

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

For agents with many skills and rich conditions, add a `matches` classmethod to each skill:

```python
class SkillA(MySkillBase):
    @classmethod
    def matches(cls, parsed: ParsedMessage, user: dict) -> bool:
        return "keyword" in parsed.signals
```

Then iterate in `_pick_skill`: `next((n for n, cls in self._SKILLS.items() if cls.matches(parsed, user)), "default_skill")`.
This upgrade is explicit and backwards-compatible — no structural change to the package.

---

## How an Agent integrates with the 4-layer pipeline

```
WhatsApp POST /webhook
  → onboarding gate           [handlers/onboarding_handler.py]
  → pending_expense gate      [handlers/pending_expense_handler.py]
  → pending_event gate        [handlers/pending_event_handler.py]
  → pending_<domain> gate     [handlers/pending_<domain>_handler.py]  ← one per stateful agent
  → parse_message()           Layer 1  [parser/message_parser.py]
  → route(parsed)             Layer 2  [router/deterministic_router.py]
  → agent.execute(parsed,user)Layer 3  [agents/<domain>/agent.py]
  → format_response(result)   Layer 4  [responder/response_formatter.py]
  → send_whatsapp_message()
```

**Invariants that must never break:**
- The router (`route()`) still returns `BaseAgent` instances. New agents add one import + one `if signals & KEYWORDS` line.
- `execute(parsed, user) → AgentResult` is the only contract Layer 2 and Layer 4 know about.
- Skills are invisible to everything outside the agent package.
- Gates are thin: detect state → call `agent.run_skill(name, ctx)` → `format_response` → `send`. No domain logic in the gate itself.
- `whatsapp_webhook.py` stays thin. All new pre-pipeline state → own `handlers/*.py` gate (Hard Rule #5).

---

## TravelAgent — reference implementation

`app/agents/travel_agent/` is the canonical example of the pattern.

| File | Purpose |
|---|---|
| `__init__.py` | Re-exports `TravelAgent`. Import site unchanged when file → package. |
| `agent.py` | `_SKILLS` dict registry. `execute` → `_pick_skill_from_router` → `_run`. `run_skill` for gate entry. |
| `skill_context.py` | `SkillContext` (frozen) + `SkillResult`. |
| `skills/base.py` | `TravelSkill` ABC. |
| `skills/next_event_travel.py` | Computes leave time for the next calendar event. Stashes `pending_travel` when no location. |
| `skills/resolve_event_location.py` | Geocodes a user-supplied place name, computes leave time. |
| `skills/schedule_departure_reminder.py` | Persists a one-off departure reminder to `scheduled_reminders`. |
| `_shared/event_selection.py` | Tz-aware next-upcoming-event picker (shared by skills). |
| `_shared/leave_time.py` | `compute_leave_decision` helper. |

Gate: `app/handlers/pending_travel_handler.py` — two-step state machine (`awaiting_location` / `awaiting_reminder_confirmation`).

Repository: `app/repositories/scheduled_reminder_repository.py` — `create`, `list_due_within`, `mark_sent`.

Cron delivery: `_run_departure_reminders()` in `app/api/cron_routes.py` (5th pass, 15-min cadence).

---

## Scaling notes

These refactors are flagged for when Otto has ~10+ agents:

1. **Layer 1 keyword sprawl** — move per-domain keyword sets into the agent package and have `_scan_signals` compose the union from a registry. Removes parser edits when adding a new agent.
2. **Layer 2 router branching** — same registry approach: each agent declares its routing predicate; the router iterates. Removes router edits when adding a new agent.
3. **Agent folder grouping** — if `app/agents/` grows beyond ~15 packages, group by product area (`app/agents/productivity/`, `app/agents/finance/`). Agents are self-contained so the move is a rename.

---

## Adding a New Agent (checklist)

1. Create `app/agents/<domain>/` package following the folder structure above.
2. Define `SkillContext` and `SkillResult` in `skill_context.py` (copy from TravelAgent, adjust if needed).
3. Define the skill ABC in `skills/base.py`.
4. Implement at least one skill in `skills/<name>.py`. Follow skill rules (no LLM, no WhatsApp, no formatting).
5. Register skills in `_SKILLS` in `agent.py`. Implement `_pick_skill` deterministically.
6. Export the Agent class from `__init__.py`.
7. Add import + routing rule in `router/deterministic_router.py` (respect existing priority order; travel before calendar is a hard rule).
8. Add keyword set to `parser/message_parser.py` (`_scan_signals`) if the new domain has unique trigger words.
9. Add `_FALLBACKS` and `_ERROR_MESSAGES` entries in `responder/response_formatter.py`.
10. Add agent-specific formatting instructions in `FORMATTING_PROMPT` (use `[variable]` not `{variable}`).
11. If the agent has multi-turn state (like TravelAgent), create `handlers/pending_<domain>_handler.py` and register it in `whatsapp_webhook.py` (after existing gates, before the pipeline).
12. If the agent schedules async work, add a cron pass in `cron_routes.py` + a repository in `repositories/`.
13. Update `CLAUDE.md` (Firestore schema if changed, file structure, Hard Rules if new invariants).
14. Never touch `whatsapp_webhook.py` beyond gate registration.

## Adding a New Skill to an Existing Agent (checklist)

1. Create `app/agents/<domain>/skills/<skill_name>.py` implementing the domain ABC.
2. Register it in `_SKILLS` in `agent.py` (one line).
3. If a new entry point is needed: extend `_pick_skill` with the new condition (deterministic).
4. If the skill needs a new gate step: extend the pending handler's `step` state machine.
5. If the skill has a new success `type`: add the LLM prompt instructions OR a hardcoded short-circuit in `format_response`.
6. If new failure modes: add to `_SPECIFIC_ERRORS` in the responder.
7. No changes to the router or parser required unless the skill has new trigger keywords.

---

## Agents on the flat pattern (migrate later)

The following agents still use the original flat-file pattern. They work correctly and will be migrated one-by-one in future tasks — do not change them as part of adding new functionality:

- `app/agents/expense_agent.py`
- `app/agents/calendar_agent.py`
- `app/agents/summary_agent.py`
- `app/agents/weather_agent.py`
- `app/agents/greeting_agent.py`
- `app/agents/ambiguity_agent.py`
