"""
Smoke + unit tests for the Microsoft / multi-provider calendar integration.

Covers: provider detection, the connected_accounts model + legacy-compat
shim, the merged accessor (incl. error isolation), the Graph→Google event
shape mapping, the second-account gate, and OAuth env guards.

Firebase is stubbed by the root conftest.py.
"""
from unittest.mock import patch, MagicMock

import pytest

from app.models.inbound_message import InboundMessage
from app.services.provider_detect import detect_provider
from app.repositories.user_repository import UserRepository


# ── provider_detect ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("Gmail", "google"),
    ("uso google", "google"),
    ("mi correo es de Google", "google"),
    ("Outlook", "microsoft"),
    ("tengo hotmail", "microsoft"),
    ("óutlook", "microsoft"),          # accent-insensitive
    ("office 365", "microsoft"),
    ("no sé", None),
    ("", None),
    ("gmail y outlook", None),         # ambiguous → re-ask
])
def test_detect_provider(text, expected):
    assert detect_provider(text) == expected


# ── connected_accounts model (user_repository) ──────────────────────────────

def _patch_db(snapshot_dict, exists=True):
    """Patch user_repository.db so .document().get() returns a controllable
    snapshot. Returns the patcher context manager."""
    snap = MagicMock()
    snap.exists = exists
    snap.to_dict.return_value = snapshot_dict
    mdb = MagicMock()
    mdb.collection.return_value.document.return_value.get.return_value = snap
    return patch("app.repositories.user_repository.db", mdb)


@patch.object(UserRepository, "create_or_update_user")
def test_save_primary_google_sets_legacy_mirror(mock_cou):
    with _patch_db({}):
        UserRepository.save_connected_account(
            "+57", provider="google", encrypted_refresh_token="g1", slot="primary"
        )
    update = mock_cou.call_args[0][1]
    accts = update["connected_accounts"]
    assert len(accts) == 1
    assert accts[0]["provider"] == "google" and accts[0]["is_primary"] is True
    assert update["has_connected_calendar"] is True
    # Legacy fields kept in sync for untouched Google consumers.
    assert update["google_calendar_refresh_token"] == "g1"
    assert update["google_calendar_connected"] is True


@patch.object(UserRepository, "create_or_update_user")
def test_add_secondary_microsoft_keeps_google_primary(mock_cou):
    existing = {"connected_accounts": [
        {"provider": "google", "refresh_token": "g1", "is_primary": True,
         "created_at": "t0"},
    ]}
    with _patch_db(existing):
        UserRepository.save_connected_account(
            "+57", provider="microsoft", encrypted_refresh_token="m1",
            slot="secondary",
        )
    accts = mock_cou.call_args[0][1]["connected_accounts"]
    assert len(accts) == 2
    assert accts[0]["provider"] == "google" and accts[0]["is_primary"]
    assert accts[1]["provider"] == "microsoft" and not accts[1]["is_primary"]
    # Primary still Google → legacy mirror unchanged.
    assert mock_cou.call_args[0][1]["google_calendar_refresh_token"] == "g1"


@patch.object(UserRepository, "create_or_update_user")
def test_legacy_only_user_seeded_then_secondary_added(mock_cou):
    """A pre-existing Google-only user (flat token, no connected_accounts)
    must not lose their account when a 2nd is added."""
    with _patch_db({"google_calendar_refresh_token": "legacy-g"}):
        UserRepository.save_connected_account(
            "+57", provider="microsoft", encrypted_refresh_token="m1",
            slot="secondary",
        )
    accts = mock_cou.call_args[0][1]["connected_accounts"]
    assert [a["provider"] for a in accts] == ["google", "microsoft"]
    assert accts[0]["refresh_token"] == "legacy-g"


@patch.object(UserRepository, "create_or_update_user")
def test_cap_enforced_at_two(mock_cou):
    existing = {"connected_accounts": [
        {"provider": "google", "refresh_token": "g1", "is_primary": True},
        {"provider": "microsoft", "refresh_token": "m1", "is_primary": False},
    ]}
    with _patch_db(existing):
        UserRepository.save_connected_account(
            "+57", provider="microsoft", encrypted_refresh_token="m2",
            slot="secondary",
        )
    accts = mock_cou.call_args[0][1]["connected_accounts"]
    assert len(accts) == 2                       # never grows past 2
    assert accts[1]["refresh_token"] == "m2"     # re-link replaces slot


@patch.object(UserRepository, "create_or_update_user")
def test_clear_connected_account_recomputes_mirror(mock_cou):
    existing = {"connected_accounts": [
        {"provider": "google", "refresh_token": "g1", "is_primary": True},
        {"provider": "microsoft", "refresh_token": "m1", "is_primary": False},
    ]}
    with _patch_db(existing):
        UserRepository.clear_connected_account("+57", "google")
    update = mock_cou.call_args[0][1]
    accts = update["connected_accounts"]
    assert [a["provider"] for a in accts] == ["microsoft"]
    assert accts[0]["is_primary"] is True            # promoted
    assert update["has_connected_calendar"] is True
    # Primary no longer Google → legacy mirror cleared.
    assert update["google_calendar_connected"] is False


def test_count_and_has_any_calendar_helpers():
    assert UserRepository.count_connected_accounts({}) == 0
    assert UserRepository.count_connected_accounts(
        {"google_calendar_refresh_token": "x"}) == 1
    assert UserRepository.count_connected_accounts(
        {"connected_accounts": [{}, {}]}) == 2
    assert UserRepository._has_any_calendar({"connected_accounts": [{}]}) is True
    assert UserRepository._has_any_calendar(
        {"google_calendar_refresh_token": "x"}) is True
    assert UserRepository._has_any_calendar({}) is False


# ── calendar_accounts merged accessor ───────────────────────────────────────

def test_iter_legacy_shim_surfaces_one_primary_google():
    from app.services import calendar_accounts
    with patch.object(calendar_accounts, "decrypt", side_effect=lambda x: x):
        accts = calendar_accounts.iter_calendar_accounts(
            {"google_calendar_refresh_token": "enc-g"})
    assert len(accts) == 1
    assert accts[0]["provider"] == "google"
    assert accts[0]["is_primary"] is True
    assert accts[0]["refresh_token"] == "enc-g"


def test_merge_combines_both_providers():
    from app.services import calendar_accounts
    user = {"connected_accounts": [
        {"provider": "google", "refresh_token": "g", "is_primary": True},
        {"provider": "microsoft", "refresh_token": "m", "is_primary": False},
    ]}
    with patch.object(calendar_accounts, "decrypt", side_effect=lambda x: x), \
         patch.object(calendar_accounts.google_calendar,
                      "get_today_events_for_user", return_value=[{"id": "g1"}]), \
         patch.object(calendar_accounts.microsoft_calendar,
                      "get_today_events_for_user", return_value=[{"id": "m1"}]):
        events = calendar_accounts.get_today_events_merged(user)
    assert {e["id"] for e in events} == {"g1", "m1"}


def test_merge_isolates_dead_secondary():
    """A dead secondary token must not break the merge — primary still answers."""
    from app.services import calendar_accounts
    from app.services.google_calendar import CalendarTokenInvalid
    user = {"connected_accounts": [
        {"provider": "google", "refresh_token": "g", "is_primary": True},
        {"provider": "microsoft", "refresh_token": "m", "is_primary": False},
    ]}
    with patch.object(calendar_accounts, "decrypt", side_effect=lambda x: x), \
         patch.object(calendar_accounts.google_calendar,
                      "get_today_events_for_user", return_value=[{"id": "g1"}]), \
         patch.object(calendar_accounts.microsoft_calendar,
                      "get_today_events_for_user",
                      side_effect=CalendarTokenInvalid("dead")):
        events = calendar_accounts.get_today_events_merged(user)
    assert [e["id"] for e in events] == ["g1"]


def test_dead_primary_raises_with_provider():
    from app.services import calendar_accounts
    from app.services.google_calendar import CalendarTokenInvalid
    user = {"connected_accounts": [
        {"provider": "microsoft", "refresh_token": "m", "is_primary": True},
    ]}
    with patch.object(calendar_accounts, "decrypt", side_effect=lambda x: x), \
         patch.object(calendar_accounts.microsoft_calendar,
                      "get_today_events_for_user",
                      side_effect=CalendarTokenInvalid("dead")):
        with pytest.raises(CalendarTokenInvalid) as ei:
            calendar_accounts.get_today_events_merged(user, strict_primary=True)
    assert getattr(ei.value, "provider", None) == "microsoft"


def test_no_accounts_raises_value_error():
    from app.services import calendar_accounts
    with pytest.raises(ValueError, match="calendar_not_connected"):
        calendar_accounts.get_today_events_merged({})


def test_create_event_targets_primary_provider():
    from app.services import calendar_accounts
    user = {"connected_accounts": [
        {"provider": "microsoft", "refresh_token": "m", "is_primary": True},
        {"provider": "google", "refresh_token": "g", "is_primary": False},
    ]}
    with patch.object(calendar_accounts, "decrypt", side_effect=lambda x: x), \
         patch.object(calendar_accounts.microsoft_calendar,
                      "create_event_for_user", return_value={"id": "evt"}) as mc, \
         patch.object(calendar_accounts.google_calendar,
                      "create_event_for_user") as gc:
        out = calendar_accounts.create_event_on_primary(
            user, title="X", start_iso="2026-05-17T10:00:00+00:00",
            end_iso="2026-05-17T11:00:00+00:00", timezone_str="UTC")
    assert out == {"id": "evt"}
    mc.assert_called_once()
    gc.assert_not_called()


# ── microsoft_calendar Graph→Google shape mapping ───────────────────────────

def test_normalize_graph_event_maps_to_google_shape():
    from app.services.microsoft_calendar import _normalize_graph_event
    graph_ev = {
        "id": "abc",
        "subject": "  Standup  ",
        "location": {"displayName": "Room 2"},
        "onlineMeeting": {"joinUrl": "https://teams/x"},
        "start": {"dateTime": "2026-05-17T15:00:00.0000000", "timeZone": "UTC"},
        "end": {"dateTime": "2026-05-17T15:30:00.0000000", "timeZone": "UTC"},
    }
    ev = _normalize_graph_event(graph_ev)
    assert ev["id"] == "abc"
    assert ev["summary"] == "Standup"
    assert ev["location"] == "Room 2"
    assert ev["description"] == "https://teams/x"          # online link surfaced
    assert ev["start"]["dateTime"] == "2026-05-17T15:00:00+00:00"
    assert ev["end"]["dateTime"] == "2026-05-17T15:30:00+00:00"

    # And the existing Google normalizer accepts the shape unchanged.
    from app.services.google_calendar import normalize_events
    norm = normalize_events([ev])
    assert norm[0]["title"] == "Standup"
    assert norm[0]["is_virtual"] is True


def test_to_iso_handles_missing_and_malformed():
    from app.services.microsoft_calendar import _to_iso
    assert _to_iso(None) is None
    assert _to_iso({}) is None
    assert _to_iso({"dateTime": "2026-05-17T09:00:00.0000000"}) == \
        "2026-05-17T09:00:00+00:00"


# ── microsoft_oauth env guards (no network) ─────────────────────────────────

def test_microsoft_oauth_requires_env(monkeypatch):
    from app.services import microsoft_oauth
    monkeypatch.delenv("MICROSOFT_OAUTH_CLIENT_ID", raising=False)
    with pytest.raises(RuntimeError, match="MICROSOFT_OAUTH_CLIENT_ID"):
        microsoft_oauth._build_app()


# ── account_link_handler (second-account gate) ──────────────────────────────

def _inbound(text):
    return InboundMessage(
        user_phone_number="+573001234567",
        message_id="m1",
        message_type="text",
        text=text,
    )


def _completed_user(**extra):
    u = {"language": "es", "onboarding_state": "completed", "name": "Otto"}
    u.update(extra)
    return u


@patch("app.handlers.account_link_handler.send_whatsapp_message")
def test_account_link_ignores_unrelated_message(mock_send):
    from app.handlers.account_link_handler import handle_account_link
    from app.db.user_context_store import update_user_context
    update_user_context("+573001234567", "pending_account_link", None)
    assert handle_account_link(_inbound("¿qué tengo hoy?"), _completed_user()) is False
    mock_send.assert_not_called()


@patch("app.handlers.account_link_handler.send_whatsapp_message")
def test_account_link_not_active_during_onboarding(mock_send):
    from app.handlers.account_link_handler import handle_account_link
    from app.db.user_context_store import update_user_context
    update_user_context("+573001234567", "pending_account_link", None)
    user = {"language": "en", "onboarding_state": "oauth_pending"}
    assert handle_account_link(_inbound("add my second email"), user) is False


@patch("app.handlers.account_link_handler.send_whatsapp_message")
def test_account_link_cap_reached(mock_send):
    from app.handlers.account_link_handler import handle_account_link
    from app.db.user_context_store import update_user_context
    update_user_context("+573001234567", "pending_account_link", None)
    user = _completed_user(connected_accounts=[{"provider": "google"},
                                               {"provider": "microsoft"}])
    assert handle_account_link(_inbound("add my second email"), user) is True
    assert "2" in mock_send.call_args[0][1]      # "limit reached (max 2)"


@patch("app.handlers.account_link_handler.UserRepository.set_oauth_state_token")
@patch("app.handlers.account_link_handler.send_whatsapp_message")
def test_account_link_full_flow(mock_send, mock_set):
    from app.handlers.account_link_handler import handle_account_link
    from app.db.user_context_store import get_user_context, update_user_context
    update_user_context("+573001234567", "pending_account_link", None)
    user = _completed_user(connected_accounts=[{"provider": "google"}])

    # 1. user asks to add a second account → Otto asks which provider
    assert handle_account_link(_inbound("quiero agregar mi segundo correo"), user) is True
    assert get_user_context("+573001234567")["pending_account_link"]["step"] == "awaiting_provider"

    # 2. user answers provider → link minted as secondary slot
    assert handle_account_link(_inbound("outlook"), user) is True
    mock_set.assert_called_once()
    assert mock_set.call_args.kwargs["provider"] == "microsoft"
    assert mock_set.call_args.kwargs["slot"] == "secondary"
    assert get_user_context("+573001234567").get("pending_account_link") is None


@patch("app.handlers.account_link_handler.send_whatsapp_message")
def test_account_link_provider_retry_then_abort(mock_send):
    from app.handlers.account_link_handler import handle_account_link
    from app.db.user_context_store import update_user_context
    update_user_context("+573001234567", "pending_account_link",
                        {"step": "awaiting_provider"})
    user = _completed_user()
    # unrecognized provider → re-ask, stays pending
    assert handle_account_link(_inbound("no sé"), user) is True
    # abort → cleared
    assert handle_account_link(_inbound("cancela"), user) is True
