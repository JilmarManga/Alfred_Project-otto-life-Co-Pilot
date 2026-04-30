"""
Tests for Fix 1: Onboarding language detection word-boundary check.

Before the fix, "es" in text_lower would match "yes", "these", etc.
After the fix, matching uses .split() so "es" must be a standalone word.
"""

import asyncio
from unittest.mock import patch
import pytest
from app.handlers.onboarding_handler import handle_onboarding
from app.models.inbound_message import InboundMessage


def make_inbound(text: str) -> InboundMessage:
    return InboundMessage(
        user_phone_number="+573001234567",
        message_id="msg_test_001",
        message_type="text",
        text=text,
    )


def make_user_no_language() -> dict:
    """User exists in Firestore but hasn't picked a language yet."""
    return {"language": None, "onboarding_completed": False}


def _language_writes(mock_update) -> list:
    """Return language values written via create_or_update_user (positional args).

    The handler writes language separately from state advancement, and
    set_onboarding_state internally also calls create_or_update_user — so the
    same mock sees multiple calls per onboarding step. We only care about the
    ones that wrote a `language` key.
    """
    return [
        c.args[1]["language"]
        for c in mock_update.call_args_list
        if len(c.args) >= 2 and isinstance(c.args[1], dict) and "language" in c.args[1]
    ]


# ── Success cases: Spanish ──────────────────────────────────────────────────

@patch("app.handlers.onboarding_handler.send_whatsapp_message")
@patch("app.handlers.onboarding_handler.UserRepository.create_or_update_user")
def test_español_detected_as_spanish(mock_update, mock_send):
    result = asyncio.run(handle_onboarding(make_inbound("español"), make_user_no_language()))
    assert result is True
    assert _language_writes(mock_update) == ["es"]


@patch("app.handlers.onboarding_handler.send_whatsapp_message")
@patch("app.handlers.onboarding_handler.UserRepository.create_or_update_user")
def test_español_capitalized_detected(mock_update, mock_send):
    result = asyncio.run(handle_onboarding(make_inbound("Español"), make_user_no_language()))
    assert result is True
    assert _language_writes(mock_update) == ["es"]


@patch("app.handlers.onboarding_handler.send_whatsapp_message")
@patch("app.handlers.onboarding_handler.UserRepository.create_or_update_user")
def test_es_standalone_detected_as_spanish(mock_update, mock_send):
    result = asyncio.run(handle_onboarding(make_inbound("es"), make_user_no_language()))
    assert result is True
    assert _language_writes(mock_update) == ["es"]


@patch("app.handlers.onboarding_handler.send_whatsapp_message")
@patch("app.handlers.onboarding_handler.UserRepository.create_or_update_user")
def test_si_español_detected_as_spanish(mock_update, mock_send):
    # "sí, español" splits to ["sí,", "español"] — "español" is in the list
    result = asyncio.run(handle_onboarding(make_inbound("sí, español"), make_user_no_language()))
    assert result is True
    assert _language_writes(mock_update) == ["es"]


# ── Success cases: English ──────────────────────────────────────────────────

@patch("app.handlers.onboarding_handler.send_whatsapp_message")
@patch("app.handlers.onboarding_handler.UserRepository.create_or_update_user")
def test_english_detected(mock_update, mock_send):
    result = asyncio.run(handle_onboarding(make_inbound("English"), make_user_no_language()))
    assert result is True
    assert _language_writes(mock_update) == ["en"]


@patch("app.handlers.onboarding_handler.send_whatsapp_message")
@patch("app.handlers.onboarding_handler.UserRepository.create_or_update_user")
def test_en_standalone_detected(mock_update, mock_send):
    result = asyncio.run(handle_onboarding(make_inbound("en"), make_user_no_language()))
    assert result is True
    assert _language_writes(mock_update) == ["en"]


# ── Regression cases: false positives that were the bug ────────────────────

@patch("app.handlers.onboarding_handler.send_whatsapp_message")
@patch("app.handlers.onboarding_handler.UserRepository.create_or_update_user")
def test_yes_does_not_match_spanish(mock_update, mock_send):
    """'yes' contains 'es' as a substring — must NOT be detected as Spanish."""
    result = asyncio.run(handle_onboarding(make_inbound("yes"), make_user_no_language()))
    assert result is True
    assert _language_writes(mock_update) == []  # no language saved
    sent_text = mock_send.call_args[0][1]
    assert "Español" in sent_text or "English" in sent_text  # retry prompt


@patch("app.handlers.onboarding_handler.send_whatsapp_message")
@patch("app.handlers.onboarding_handler.UserRepository.create_or_update_user")
def test_these_does_not_match(mock_update, mock_send):
    """'these' contains 'es' as substring — must NOT match."""
    result = asyncio.run(handle_onboarding(make_inbound("these"), make_user_no_language()))
    assert result is True
    assert _language_writes(mock_update) == []
