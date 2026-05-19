"""
ReminderAgent (personal reminders) — end-to-end suite.

Covers the 10 verification scenarios from the plan: set (specific / part-of-day
/ no-time clarify), reminder-vs-event ambiguity, cron delivery + 10-min
follow-up window (gate path + cron sweep), list, cancel, routing-precedence
regressions, and cron isolation from TravelAgent's departure reminders.
External I/O (Firestore via UserReminderRepository, WhatsApp) is mocked.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

import pytest

from app.models.parsed_message import ParsedMessage
from app.parser.message_parser import _scan_signals
from app.router.deterministic_router import route

from app.agents.reminder_agent import ReminderAgent
from app.agents.expense_agent import ExpenseAgent
from app.agents.calendar_agent import CalendarAgent
from app.agents.list_agent import ListAgent

import app.db.user_context_store as ucs

USER = {"phone_number": "+573001234567", "language": "en", "name": "Otto",
        "timezone": "America/Bogota", "preferred_currency": "COP"}

_REPO_PATHS = [
    "app.agents.reminder_agent.skills.set_reminder.UserReminderRepository",
    "app.agents.reminder_agent.skills.list_reminders.UserReminderRepository",
    "app.agents.reminder_agent.skills.cancel_reminder.UserReminderRepository",
    "app.agents.reminder_agent.skills.reschedule_reminder.UserReminderRepository",
    "app.handlers.pending_reminder_handler.UserReminderRepository",
]


@pytest.fixture(autouse=True)
def _clear_context():
    ucs.USER_CONTEXT.clear()
    yield
    ucs.USER_CONTEXT.clear()


def _pm(text, **kw):
    return ParsedMessage(raw_message=text, signals=_scan_signals(text), **kw)


def _inbound(text):
    m = MagicMock()
    m.user_phone_number = USER["phone_number"]
    m.text = text
    return m


# ── 1. Routing precedence / regressions ─────────────────────────────────────

def test_route_reminder_set():
    p = _pm("remind me to call my mom", reminder_intent="set",
            reminder_text="call my mom")
    assert isinstance(route(p).agent, ReminderAgent)


def test_route_toggle_setting_still_calendar_not_reminder():
    # "disable reminders" is the calendar on/off SETTING — priority 0.
    p = _pm("disable reminders")
    assert isinstance(route(p).agent, CalendarAgent)
    assert ReminderAgent.matches(p) is False


def test_route_expense_wins_over_reminder_when_amount():
    p = _pm("recuérdame que gasté 50000 en el gym", amount=50000.0,
            reminder_intent="set", reminder_text="gasté en el gym")
    assert isinstance(route(p).agent, ExpenseAgent)


def test_route_list_unaffected():
    p = _pm("guarda esto en mi lista", list_intent="save", list_item="esto")
    assert isinstance(route(p).agent, ListAgent)


def test_route_reminder_list_and_cancel_intents():
    assert isinstance(route(_pm("what reminders do I have",
                                reminder_intent="list")).agent, ReminderAgent)
    assert isinstance(route(_pm("cancel the gym reminder",
                                reminder_intent="cancel",
                                reminder_cancel_ref="gym")).agent, ReminderAgent)


# ── 2-3. set_reminder: specific time & part-of-day defaults ─────────────────

def _patch_repo():
    return patch.multiple(
        "app.agents.reminder_agent.skills.set_reminder",
        UserReminderRepository=MagicMock(),
    )


def test_set_specific_time_persists_exact_fire_at():
    with patch("app.agents.reminder_agent.skills.set_reminder.UserReminderRepository") as repo:
        p = _pm("remind me to call mom tomorrow at 3pm", reminder_intent="set",
                reminder_text="call mom", reminder_time="2026-05-19T15:00:00-05:00")
        result = ReminderAgent().execute(p, USER)
    assert result.success and result.data["type"] == "reminder_set"
    kwargs = repo.create.call_args.kwargs
    assert kwargs["fire_at_iso"] == "2026-05-19T15:00:00-05:00"
    assert kwargs["reminder_text"] == "call mom"
    assert kwargs["user_phone_number"] == USER["phone_number"]


@pytest.mark.parametrize("period,hour", [("morning", 9), ("afternoon", 15), ("night", 19)])
def test_set_part_of_day_defaults(period, hour):
    with patch("app.agents.reminder_agent.skills.set_reminder.UserReminderRepository") as repo:
        p = _pm(f"remind me to call mom tomorrow {period}", reminder_intent="set",
                reminder_text="call mom",
                reminder_time="2026-12-01T00:00:00-05:00",
                reminder_period=period)
        ReminderAgent().execute(p, USER)
    fire = repo.create.call_args.kwargs["fire_at_iso"]
    dt = datetime.fromisoformat(fire)
    assert (dt.hour, dt.minute) == (hour, 0)
    assert dt.date().isoformat() == "2026-12-01"


# ── 4. No time-of-day → clarify gate, then completion ──────────────────────

def test_no_time_stashes_clarify_then_completes():
    with patch("app.agents.reminder_agent.skills.set_reminder.UserReminderRepository") as repo:
        p = _pm("remind me to call my mom", reminder_intent="set",
                reminder_text="call my mom")
        result = ReminderAgent().execute(p, USER)
        assert result.data["type"] == "reminder_need_time"
        pending = ucs.get_user_context(USER["phone_number"])["pending_reminder"]
        assert pending["step"] == "awaiting_time_of_day"

        from app.handlers.pending_reminder_handler import handle_pending_reminder
        with patch("app.handlers.pending_reminder_handler.send_whatsapp_message") as snd:
            consumed = handle_pending_reminder(_inbound("in the morning"), USER)
    assert consumed is True
    assert repo.create.called
    fire = datetime.fromisoformat(repo.create.call_args.kwargs["fire_at_iso"])
    assert (fire.hour, fire.minute) == (9, 0)
    assert "pending_reminder" not in ucs.get_user_context(USER["phone_number"]) \
        or ucs.get_user_context(USER["phone_number"]).get("pending_reminder") is None
    assert snd.called


# ── 5. Reminder-vs-event ambiguity ─────────────────────────────────────────

def test_ambiguous_stages_clarify():
    p = _pm("remind me about the dentist tomorrow at 3pm", reminder_intent="set",
            reminder_text="dentist", reminder_time="2026-05-19T15:00:00-05:00",
            event_title="dentist", event_start="2026-05-19T15:00:00-05:00")
    result = ReminderAgent().execute(p, USER)
    assert result.data["type"] == "reminder_or_event"
    assert ucs.get_user_context(USER["phone_number"])["pending_reminder"]["step"] \
        == "awaiting_reminder_or_event"


def test_ambiguity_reply_calendar_routes_to_calendar():
    p = _pm("remind me about the dentist tomorrow at 3pm", reminder_intent="set",
            reminder_text="dentist", reminder_time="2026-05-19T15:00:00-05:00",
            event_title="dentist", event_start="2026-05-19T15:00:00-05:00")
    ReminderAgent().execute(p, USER)
    from app.handlers.pending_reminder_handler import handle_pending_reminder
    with patch("app.handlers.pending_reminder_handler.send_whatsapp_message"), \
         patch("app.agents.calendar_agent.CalendarAgent.execute") as cal:
        cal.return_value = MagicMock(data={}, success=True)
        consumed = handle_pending_reminder(_inbound("calendar"), USER)
    assert consumed is True
    cal.assert_called_once()


def test_ambiguity_reply_reminder_creates():
    p = _pm("remind me about the dentist tomorrow at 3pm", reminder_intent="set",
            reminder_text="dentist", reminder_time="2026-05-19T15:00:00-05:00",
            event_title="dentist", event_start="2026-05-19T15:00:00-05:00")
    ReminderAgent().execute(p, USER)
    from app.handlers.pending_reminder_handler import handle_pending_reminder
    with patch("app.agents.reminder_agent.skills.set_reminder.UserReminderRepository") as repo, \
         patch("app.handlers.pending_reminder_handler.send_whatsapp_message"):
        consumed = handle_pending_reminder(_inbound("just remind me"), USER)
    assert consumed is True
    assert repo.create.called


# ── 6-7. Cron delivery + 10-min follow-up window ───────────────────────────

def _due_doc(doc_id="r1"):
    return {"id": doc_id, "user_phone_number": USER["phone_number"],
            "reminder_text": "call mom", "lang": "en",
            "fire_at": datetime.now(timezone.utc).isoformat(), "tz": "America/Bogota"}


def test_cron_delivers_and_flips_to_awaiting_followup():
    from app.api.cron_routes import _run_user_reminders
    repo = MagicMock()
    repo.list_due_scheduled.return_value = [_due_doc()]
    with patch("app.repositories.user_reminder_repository.UserReminderRepository", repo), \
         patch("app.api.cron_routes.send_whatsapp_message") as snd:
        n = _run_user_reminders()
    assert n == 1
    assert snd.call_count == 2  # reminder line + follow-up question
    repo.mark_awaiting_followup.assert_called_once()
    assert repo.mark_awaiting_followup.call_args[0][0] == "r1"


def _awaiting_doc(delivered_minutes_ago=2, doc_id="r1"):
    return {"id": doc_id, "user_phone_number": USER["phone_number"],
            "reminder_text": "call mom", "lang": "en", "tz": "America/Bogota",
            "status": "awaiting_followup",
            "delivered_at": (datetime.now(timezone.utc)
                             - timedelta(minutes=delivered_minutes_ago)).isoformat()}


@pytest.mark.parametrize("reply,mode", [
    ("at 7pm", "reschedule"),
    ("in an hour", "reschedule"),
    ("delete it", "delete"),
])
def test_post_delivery_actions(reply, mode):
    from app.handlers.pending_reminder_handler import handle_pending_reminder
    repo = MagicMock()
    repo.list_awaiting_followup_for_phone.return_value = [_awaiting_doc()]
    with patch("app.handlers.pending_reminder_handler.UserReminderRepository", repo), \
         patch("app.agents.reminder_agent.skills.reschedule_reminder.UserReminderRepository", repo), \
         patch("app.handlers.pending_reminder_handler.send_whatsapp_message"):
        consumed = handle_pending_reminder(_inbound(reply), USER)
    assert consumed is True
    if mode == "delete":
        repo.delete.assert_called_once_with("r1")
        repo.reschedule.assert_not_called()
    else:
        repo.reschedule.assert_called_once()
        assert repo.reschedule.call_args[0][0] == "r1"


def test_post_delivery_stale_gate_drops_doc():
    from app.handlers.pending_reminder_handler import handle_pending_reminder
    repo = MagicMock()
    repo.list_awaiting_followup_for_phone.return_value = [_awaiting_doc(delivered_minutes_ago=11)]
    with patch("app.handlers.pending_reminder_handler.UserReminderRepository", repo):
        consumed = handle_pending_reminder(_inbound("at 7pm"), USER)
    assert consumed is False  # late → pipeline handles message
    repo.delete.assert_called_once_with("r1")
    repo.reschedule.assert_not_called()


def test_post_delivery_unrelated_deletes_and_falls_through():
    from app.handlers.pending_reminder_handler import handle_pending_reminder
    repo = MagicMock()
    repo.list_awaiting_followup_for_phone.return_value = [_awaiting_doc()]
    with patch("app.handlers.pending_reminder_handler.UserReminderRepository", repo):
        consumed = handle_pending_reminder(_inbound("what's the weather today"), USER)
    assert consumed is False
    repo.delete.assert_called_once_with("r1")


def test_cron_sweep_deletes_stale_followups():
    from app.api.cron_routes import _sweep_stale_reminder_followups
    repo = MagicMock()
    repo.list_stale_awaiting_followup.return_value = [{"id": "r9"}]
    with patch("app.repositories.user_reminder_repository.UserReminderRepository", repo):
        swept = _sweep_stale_reminder_followups()
    assert swept == 1
    repo.delete.assert_called_once_with("r9")


# ── 8. list + cancel ───────────────────────────────────────────────────────

def test_list_reminders():
    repo = MagicMock()
    repo.list_for_phone.return_value = [
        {"id": "a", "status": "scheduled", "reminder_text": "call mom",
         "fire_at": "2026-05-19T09:00:00-05:00"},
    ]
    with patch("app.agents.reminder_agent.skills.list_reminders.UserReminderRepository", repo):
        result = ReminderAgent().execute(_pm("my reminders", reminder_intent="list"), USER)
    assert result.data["type"] == "reminder_list"
    assert result.data["reminders"][0]["reminder_text"] == "call mom"


def test_cancel_single_match():
    repo = MagicMock()
    repo.list_for_phone.return_value = [
        {"id": "a", "status": "scheduled", "reminder_text": "call the gym"},
        {"id": "b", "status": "scheduled", "reminder_text": "pay rent"},
    ]
    with patch("app.agents.reminder_agent.skills.cancel_reminder.UserReminderRepository", repo):
        result = ReminderAgent().execute(
            _pm("cancel the gym reminder", reminder_intent="cancel",
                reminder_cancel_ref="gym"), USER)
    assert result.data["type"] == "reminder_cancelled"
    repo.delete.assert_called_once_with("a")


def test_cancel_multi_match_then_choice():
    repo = MagicMock()
    repo.list_for_phone.return_value = [
        {"id": "a", "status": "scheduled", "reminder_text": "call the gym"},
        {"id": "b", "status": "scheduled", "reminder_text": "gym membership"},
    ]
    repo.get.return_value = {"reminder_text": "call the gym"}
    with patch("app.agents.reminder_agent.skills.cancel_reminder.UserReminderRepository", repo):
        result = ReminderAgent().execute(
            _pm("cancel the gym reminder", reminder_intent="cancel",
                reminder_cancel_ref="gym"), USER)
        assert result.data["type"] == "reminder_cancel_choice"
        from app.handlers.pending_reminder_handler import handle_pending_reminder
        with patch("app.handlers.pending_reminder_handler.UserReminderRepository", repo), \
             patch("app.handlers.pending_reminder_handler.send_whatsapp_message"):
            consumed = handle_pending_reminder(_inbound("1"), USER)
    assert consumed is True
    repo.delete.assert_called_once_with("a")


# ── 9-10. run_cron_job wiring + isolation from departure path ──────────────

@patch("app.api.cron_routes._sweep_stale_reminder_followups", return_value=3)
@patch("app.api.cron_routes._run_user_reminders", return_value=2)
@patch("app.api.cron_routes._run_departure_reminders", return_value=5)
@patch("app.api.cron_routes._run_morning_briefs", return_value=0)
@patch("app.api.cron_routes._run_event_reminders", return_value=0)
@patch("app.api.cron_routes.UserRepository.list_pending_location_retries", return_value=[])
@patch("app.api.cron_routes.UserRepository.list_pending_oauth_followups", return_value=[])
def test_run_cron_job_counts_and_isolation(mock_o, mock_l, mock_e, mock_b, mock_dep,
                                           mock_user, mock_sweep):
    from app.api.cron_routes import run_cron_job
    result = run_cron_job()
    assert result["user_reminders_sent"] == 2
    assert result["stale_followups_swept"] == 3
    assert result["departure_reminders_sent"] == 5  # untouched
    mock_dep.assert_called_once()
