"""
Smoke + E2E tests for all cron job functions.

Coverage:
1. _is_morning_brief_window — two valid ticks (6:00 and 6:15) now in range
2. send_whatsapp_message — bool return, no exception on failure, timeout present
3. _run_morning_briefs — only marks sent on confirmed delivery; never on rejected/error
4. _run_event_reminders — sends reminders, dedupes, skips all-day events
5. _run_departure_reminders — delivers and deletes on-time scheduled reminders
6. run_cron_job — all five stages run; individual failures don't cascade
"""

import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock, call
from zoneinfo import ZoneInfo

import pytest


# ── 1. _is_morning_brief_window ─────────────────────────────────────────────

from app.api.cron_routes import _is_morning_brief_window

_BOGOTA = ZoneInfo("America/Bogota")


def _tz_at(hour: int, minute: int, tz=_BOGOTA) -> ZoneInfo:
    """Return a ZoneInfo-like mock whose datetime.now() reports the given local time."""
    fake_tz = MagicMock(spec=ZoneInfo)
    fake_dt = MagicMock()
    fake_dt.hour = hour
    fake_dt.minute = minute
    with patch("app.api.cron_routes.datetime") as mock_dt:
        mock_dt.now.return_value = fake_dt
        yield fake_tz


def _window(hour: int, minute: int) -> bool:
    """Check _is_morning_brief_window with a fake local time."""
    fake_dt = MagicMock()
    fake_dt.hour = hour
    fake_dt.minute = minute
    with patch("app.api.cron_routes.datetime") as mock_dt:
        mock_dt.now.return_value = fake_dt
        return _is_morning_brief_window(MagicMock(spec=ZoneInfo))


def test_window_true_at_6_00():
    assert _window(6, 0) is True


def test_window_true_at_6_15():
    """6:15 tick — was False before fix (<15), now True (<30)."""
    assert _window(6, 15) is True


def test_window_true_at_6_29():
    assert _window(6, 29) is True


def test_window_false_at_6_30():
    assert _window(6, 30) is False


def test_window_false_at_7_00():
    assert _window(7, 0) is False


def test_window_false_at_5_59():
    assert _window(5, 59) is False


# ── 2. send_whatsapp_message — bool return & no-exception contract ───────────

from app.services.whatsapp_sender import send_whatsapp_message
import requests as _requests_module


def _mock_response(status_code: int) -> MagicMock:
    r = MagicMock()
    r.status_code = status_code
    r.text = "{}"
    return r


@patch("app.services.whatsapp_sender.requests.post")
def test_send_returns_true_on_200(mock_post):
    mock_post.return_value = _mock_response(200)
    result = send_whatsapp_message("+1", "hi")
    assert result is True


@patch("app.services.whatsapp_sender.requests.post")
def test_send_returns_false_on_401(mock_post):
    mock_post.return_value = _mock_response(401)
    result = send_whatsapp_message("+1", "hi")
    assert result is False


@patch("app.services.whatsapp_sender.requests.post")
def test_send_returns_false_on_network_error(mock_post):
    mock_post.side_effect = _requests_module.ConnectionError("timeout")
    result = send_whatsapp_message("+1", "hi")
    assert result is False


@patch("app.services.whatsapp_sender.requests.post")
def test_send_passes_timeout_to_requests(mock_post):
    mock_post.return_value = _mock_response(200)
    send_whatsapp_message("+1", "hi")
    _, kwargs = mock_post.call_args
    assert kwargs.get("timeout") == 15


# ── 3. _run_morning_briefs — mark_sent only on confirmed delivery ────────────

from app.api.cron_routes import _run_morning_briefs


def _make_user(phone="+573001234567", tz="America/Bogota", token="enc-tok"):
    return {
        "phone": phone,
        "timezone": tz,
        "google_calendar_refresh_token": token,
        "morning_brief_sent_date": None,
        "language": "es",
        "calendar_reminders_enabled": True,
    }


@patch("app.api.cron_routes.UserRepository.mark_morning_brief_sent")
@patch("app.api.cron_routes.run_morning_briefing", return_value=True)
@patch("app.api.cron_routes.decrypt", return_value="plain-tok")
@patch("app.api.cron_routes._is_morning_brief_window", return_value=True)
@patch("app.api.cron_routes.UserRepository.list_users_for_morning_brief")
def test_marks_sent_when_delivery_succeeds(mock_list, mock_window, mock_decrypt,
                                           mock_brief, mock_mark):
    mock_list.return_value = [_make_user()]
    count = _run_morning_briefs()
    assert count == 1
    mock_mark.assert_called_once()


@patch("app.api.cron_routes.UserRepository.mark_morning_brief_sent")
@patch("app.api.cron_routes.run_morning_briefing", return_value=False)
@patch("app.api.cron_routes.decrypt", return_value="plain-tok")
@patch("app.api.cron_routes._is_morning_brief_window", return_value=True)
@patch("app.api.cron_routes.UserRepository.list_users_for_morning_brief")
def test_does_not_mark_sent_when_whatsapp_rejects(mock_list, mock_window, mock_decrypt,
                                                  mock_brief, mock_mark):
    mock_list.return_value = [_make_user()]
    count = _run_morning_briefs()
    assert count == 0
    mock_mark.assert_not_called()


@patch("app.api.cron_routes.UserRepository.mark_morning_brief_sent")
@patch("app.api.cron_routes.run_morning_briefing", side_effect=Exception("calendar down"))
@patch("app.api.cron_routes.decrypt", return_value="plain-tok")
@patch("app.api.cron_routes._is_morning_brief_window", return_value=True)
@patch("app.api.cron_routes.UserRepository.list_users_for_morning_brief")
def test_does_not_mark_sent_on_exception(mock_list, mock_window, mock_decrypt,
                                         mock_brief, mock_mark):
    mock_list.return_value = [_make_user()]
    count = _run_morning_briefs()
    assert count == 0
    mock_mark.assert_not_called()


@patch("app.api.cron_routes.UserRepository.mark_morning_brief_sent")
@patch("app.api.cron_routes.run_morning_briefing", return_value=True)
@patch("app.api.cron_routes.decrypt", return_value="plain-tok")
@patch("app.api.cron_routes._is_morning_brief_window", return_value=False)
@patch("app.api.cron_routes.UserRepository.list_users_for_morning_brief")
def test_skips_user_outside_window(mock_list, mock_window, mock_decrypt,
                                   mock_brief, mock_mark):
    mock_list.return_value = [_make_user()]
    count = _run_morning_briefs()
    assert count == 0
    mock_brief.assert_not_called()
    mock_mark.assert_not_called()


@patch("app.api.cron_routes.UserRepository.mark_morning_brief_sent")
@patch("app.api.cron_routes.run_morning_briefing", return_value=True)
@patch("app.api.cron_routes.decrypt", return_value="plain-tok")
@patch("app.api.cron_routes._is_morning_brief_window", return_value=True)
@patch("app.api.cron_routes.UserRepository.list_users_for_morning_brief")
def test_skips_user_already_sent_today(mock_list, mock_window, mock_decrypt,
                                       mock_brief, mock_mark):
    user = _make_user()
    # Patch datetime.now to return today matching the stored date
    today = "2026-04-29"
    user["morning_brief_sent_date"] = today
    with patch("app.api.cron_routes.datetime") as mock_dt:
        fake_dt = MagicMock()
        fake_dt.date.return_value.isoformat.return_value = today
        fake_dt.hour = 6
        fake_dt.minute = 0
        mock_dt.now.return_value = fake_dt
        mock_list.return_value = [user]
        count = _run_morning_briefs()
    assert count == 0
    mock_brief.assert_not_called()


# ── 4. _run_event_reminders ─────────────────────────────────────────────────

from app.api.cron_routes import _run_event_reminders


def _make_reminder_user(phone="+1", notified=None):
    return {
        "phone": phone,
        "timezone": "America/Bogota",
        "google_calendar_refresh_token": "enc",
        "notified_event_ids": notified or [],
        "language": "es",
        "calendar_reminders_enabled": True,
    }


def _make_event(event_id="evt1", minutes_from_now=60):
    start = datetime.now(timezone.utc) + timedelta(minutes=minutes_from_now)
    return {
        "id": event_id,
        "summary": "Doctor",
        "start": {"dateTime": start.isoformat()},
    }


@patch("app.api.cron_routes.UserRepository.add_notified_event")
@patch("app.api.cron_routes.send_whatsapp_message", return_value=True)
@patch("app.api.cron_routes.get_upcoming_events_window")
@patch("app.api.cron_routes.decrypt", return_value="plain")
@patch("app.api.cron_routes.UserRepository.list_users_for_reminders")
def test_event_reminder_sends_and_records(mock_list, mock_decrypt, mock_calendar,
                                          mock_send, mock_add):
    mock_list.return_value = [_make_reminder_user()]
    mock_calendar.return_value = [_make_event()]
    count = _run_event_reminders()
    assert count == 1
    mock_send.assert_called_once()
    mock_add.assert_called_once()


@patch("app.api.cron_routes.UserRepository.add_notified_event")
@patch("app.api.cron_routes.send_whatsapp_message", return_value=True)
@patch("app.api.cron_routes.get_upcoming_events_window")
@patch("app.api.cron_routes.decrypt", return_value="plain")
@patch("app.api.cron_routes.UserRepository.list_users_for_reminders")
def test_event_reminder_deduped_if_already_notified(mock_list, mock_decrypt,
                                                    mock_calendar, mock_send, mock_add):
    event = _make_event()
    start_dt = datetime.fromisoformat(event["start"]["dateTime"])
    dedup_key = f"evt1:{start_dt.astimezone(ZoneInfo('America/Bogota')).date().isoformat()}"
    user = _make_reminder_user(notified=[dedup_key])
    mock_list.return_value = [user]
    mock_calendar.return_value = [event]
    count = _run_event_reminders()
    assert count == 0
    mock_send.assert_not_called()


@patch("app.api.cron_routes.UserRepository.add_notified_event")
@patch("app.api.cron_routes.send_whatsapp_message", return_value=True)
@patch("app.api.cron_routes.get_upcoming_events_window")
@patch("app.api.cron_routes.decrypt", return_value="plain")
@patch("app.api.cron_routes.UserRepository.list_users_for_reminders")
def test_event_reminder_skips_all_day_event(mock_list, mock_decrypt, mock_calendar,
                                            mock_send, mock_add):
    all_day = {"id": "evt_ad", "summary": "Birthday", "start": {"date": "2026-04-29"}}
    mock_list.return_value = [_make_reminder_user()]
    mock_calendar.return_value = [all_day]
    count = _run_event_reminders()
    assert count == 0
    mock_send.assert_not_called()


# ── 5. _run_departure_reminders ─────────────────────────────────────────────

from app.api.cron_routes import _run_departure_reminders


def _make_departure(doc_id="dep1", phone="+1", lang="es"):
    return {
        "id": doc_id,
        "user_phone_number": phone,
        "event_title": "Dentist",
        "event_location": "Calle 80",
        "lang": lang,
    }


@patch("app.api.cron_routes.send_whatsapp_message", return_value=True)
@patch("app.repositories.scheduled_reminder_repository.ScheduledReminderRepository.delete")
@patch("app.repositories.scheduled_reminder_repository.ScheduledReminderRepository.list_due_within")
def test_departure_reminder_delivers_and_deletes(mock_due, mock_delete, mock_send):
    mock_due.return_value = [_make_departure()]
    count = _run_departure_reminders()
    assert count == 1
    mock_send.assert_called_once()
    msg = mock_send.call_args[0][1]
    assert "Dentist" in msg
    mock_delete.assert_called_once_with("dep1")


@patch("app.api.cron_routes.send_whatsapp_message", return_value=True)
@patch("app.repositories.scheduled_reminder_repository.ScheduledReminderRepository.delete")
@patch("app.repositories.scheduled_reminder_repository.ScheduledReminderRepository.list_due_within")
def test_departure_reminder_includes_location(mock_due, mock_delete, mock_send):
    mock_due.return_value = [_make_departure()]
    _run_departure_reminders()
    msg = mock_send.call_args[0][1]
    assert "Calle 80" in msg


@patch("app.api.cron_routes.send_whatsapp_message", side_effect=Exception("network"))
@patch("app.repositories.scheduled_reminder_repository.ScheduledReminderRepository.delete")
@patch("app.repositories.scheduled_reminder_repository.ScheduledReminderRepository.list_due_within")
def test_departure_reminder_send_failure_does_not_delete(mock_due, mock_delete, mock_send):
    mock_due.return_value = [_make_departure()]
    count = _run_departure_reminders()
    assert count == 0
    mock_delete.assert_not_called()


# ── 6. run_cron_job — cascade isolation ─────────────────────────────────────

from app.api.cron_routes import run_cron_job


@patch("app.api.cron_routes._run_departure_reminders", return_value=0)
@patch("app.api.cron_routes._run_morning_briefs", return_value=1)
@patch("app.api.cron_routes._run_event_reminders", return_value=0)
@patch("app.api.cron_routes.UserRepository.list_pending_location_retries", return_value=[])
@patch("app.api.cron_routes.UserRepository.list_pending_oauth_followups", return_value=[])
def test_run_cron_job_returns_counts(mock_oauth, mock_loc, mock_rem, mock_brief, mock_dep):
    result = run_cron_job()
    assert result["morning_briefs_sent"] == 1
    assert result["status"] == "ok"


@patch("app.api.cron_routes._run_departure_reminders", return_value=0)
@patch("app.api.cron_routes._run_morning_briefs", side_effect=Exception("brief exploded"))
@patch("app.api.cron_routes._run_event_reminders", return_value=2)
@patch("app.api.cron_routes.UserRepository.list_pending_location_retries", return_value=[])
@patch("app.api.cron_routes.UserRepository.list_pending_oauth_followups", return_value=[])
def test_morning_brief_failure_does_not_stop_departure_reminders(mock_oauth, mock_loc,
                                                                  mock_rem, mock_brief,
                                                                  mock_dep):
    result = run_cron_job()
    mock_dep.assert_called_once()
    assert result["reminders_sent"] == 2
    assert result["morning_briefs_sent"] == 0
