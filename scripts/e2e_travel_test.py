"""
End-to-end integration tests for TravelAgent Phase 1.

Uses real APIs: OpenAI, Google Maps, Firestore.
Mocks only: send_whatsapp_message (no real WhatsApp messages sent),
            token_crypto.decrypt (no real calendar token needed — events are
            pre-loaded into user_context_store so the calendar API is never hit).

Run from repo root:
    python3 scripts/e2e_travel_test.py
"""

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

# ── bootstrap ────────────────────────────────────────────────────────────────
# Must happen before any app imports so Firebase and env vars are loaded.
os.environ.setdefault(
    "FIREBASE_CREDENTIALS_PATH",
    "credentials/firebase-service-account.json",
)
from dotenv import load_dotenv
load_dotenv()

# ── app imports ──────────────────────────────────────────────────────────────
from app.models.inbound_message import InboundMessage
from app.models.parsed_message import ParsedMessage
from app.router.deterministic_router import route
from app.responder.response_formatter import format_response
from app.db.user_context_store import get_user_context, update_user_context
from app.handlers.pending_travel_handler import handle_pending_travel
from app.api.cron_routes import _run_departure_reminders
from app.repositories.scheduled_reminder_repository import ScheduledReminderRepository
from app.core.firebase import db

# ── test helpers ─────────────────────────────────────────────────────────────

PASS = "\033[92m✓ PASS\033[0m"
FAIL = "\033[91m✗ FAIL\033[0m"

TEST_PHONE = "+15550000001_e2etest"   # clearly fake — won't clash with real users

# A real Bogotá user shape (location, timezone, language).
# google_calendar_refresh_token is fake — we patch decrypt so it's never used.
TEST_USER = {
    "phone_number": TEST_PHONE,
    "name": "E2E",
    "language": "es",
    "location": "Bogotá, Bogota, Colombia",
    "latitude": 4.710988,
    "longitude": -74.072092,
    "timezone": "America/Bogota",
    "google_calendar_connected": True,
    "google_calendar_refresh_token": "FAKE_ENCRYPTED_TOKEN",
    "calendar_reminders_enabled": True,
    "preferred_currency": "COP",
    "onboarding_state": "completed",
}

FAKE_REFRESH_TOKEN = "fake_refresh_token_for_test"


def make_inbound(text: str) -> InboundMessage:
    return InboundMessage(
        user_phone_number=TEST_PHONE,
        text=text,
        message_id="test_msg_id",
        message_type="text",
    )


def future_event_with_location() -> dict:
    start = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
    return {
        "title": "Reunión en Oficina",
        "start": start,
        "end": (datetime.now(timezone.utc) + timedelta(hours=4)).isoformat(),
        "location": "Calle 100 #15-20, Bogotá",
        "is_virtual": False,
        "meeting_link": None,
    }


def future_event_without_location() -> dict:
    start = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
    return {
        "title": "Almuerzo de trabajo",
        "start": start,
        "end": (datetime.now(timezone.utc) + timedelta(hours=4)).isoformat(),
        "location": None,
        "is_virtual": False,
        "meeting_link": None,
    }


def clear_context():
    update_user_context(TEST_PHONE, "today_events", None)
    update_user_context(TEST_PHONE, "last_referenced_event", None)
    update_user_context(TEST_PHONE, "pending_travel", None)


def cleanup_firestore_reminders():
    """Remove any scheduled_reminders docs created by this test run."""
    docs = (
        db.collection("scheduled_reminders")
        .where("user_phone_number", "==", TEST_PHONE)
        .stream()
    )
    for doc in docs:
        doc.reference.delete()


# ── tests ─────────────────────────────────────────────────────────────────────

results = []

def check(label: str, condition: bool, detail: str = ""):
    status = PASS if condition else FAIL
    suffix = f"  ({detail})" if detail else ""
    print(f"  {status}  {label}{suffix}")
    results.append((label, condition))
    return condition


# ─────────────────────────────────────────────────────────────────────────────
# A: REGRESSION — event WITH location (existing behavior must not regress)
# ─────────────────────────────────────────────────────────────────────────────
print("\n── A: Regression — event WITH location ──────────────────────────────")

with patch("app.agents.travel_agent.agent.decrypt", return_value=FAKE_REFRESH_TOKEN):
    clear_context()
    event = future_event_with_location()
    update_user_context(TEST_PHONE, "today_events", [event])

    pm = ParsedMessage(raw_message="cuándo debo salir", signals=["salir"])
    agent = route(pm)
    check("Router returns TravelAgent", agent.__class__.__name__ == "TravelAgent")

    result = agent.execute(pm, TEST_USER)
    check("execute() succeeds", result.success, f"error={result.error_message}")
    check("status is 'ok'", (result.data or {}).get("status") == "ok",
          f"status={result.data.get('status') if result.data else None}")
    check("leave_at present", bool((result.data or {}).get("leave_at")))
    check("duration_minutes > 0", ((result.data or {}).get("duration_minutes") or 0) > 0)
    check("pending_travel NOT set", get_user_context(TEST_PHONE).get("pending_travel") is None)

    reply = format_response(result, TEST_USER)
    check("Responder produces non-empty reply", bool(reply and reply.strip()))
    print(f"         → Otto: {reply[:80]!r}")


# ─────────────────────────────────────────────────────────────────────────────
# B1: Travel query on event WITHOUT location → asks for location + sets stash
# ─────────────────────────────────────────────────────────────────────────────
print("\n── B1: Event without location — asks for location ───────────────────")

with patch("app.agents.travel_agent.agent.decrypt", return_value=FAKE_REFRESH_TOKEN):
    clear_context()
    event_no_loc = future_event_without_location()
    update_user_context(TEST_PHONE, "today_events", [event_no_loc])

    pm = ParsedMessage(raw_message="cuándo debo salir", signals=["salir"])
    result = route(pm).execute(pm, TEST_USER)

    check("execute() succeeds", result.success)
    check("status is 'no_location'", (result.data or {}).get("status") == "no_location",
          f"status={result.data.get('status') if result.data else None}")
    check("title is correct", (result.data or {}).get("title") == "Almuerzo de trabajo")

    stash = get_user_context(TEST_PHONE).get("pending_travel")
    check("pending_travel stash set", stash is not None)
    check("step is 'awaiting_location'", (stash or {}).get("step") == "awaiting_location")
    check("event_title in stash", (stash or {}).get("event_title") == "Almuerzo de trabajo")
    check("event_start_iso in stash", bool((stash or {}).get("event_start_iso")))

    sent_messages = []
    with patch("app.handlers.pending_travel_handler.send_whatsapp_message",
               side_effect=lambda phone, msg: sent_messages.append(msg)):
        reply = format_response(result, TEST_USER)
        check("Responder produces non-empty reply", bool(reply and reply.strip()))
        print(f"         → Otto: {reply[:80]!r}")


# ─────────────────────────────────────────────────────────────────────────────
# B2: User replies with a real Bogotá place → geocode + compute + offer reminder
# ─────────────────────────────────────────────────────────────────────────────
print("\n── B2: User replies with place name → leave time + reminder offer ───")

import re as _re

# Keep the stash from B1
sent_b2 = []
with patch("app.handlers.pending_travel_handler.send_whatsapp_message",
           side_effect=lambda phone, msg: sent_b2.append(msg)):
    inbound = make_inbound("CC Andino")
    consumed = handle_pending_travel(inbound, TEST_USER)

# ── gate behaviour ────────────────────────────────────────────────────────────
check("gate consumed the message", consumed)
check("exactly one message sent", len(sent_b2) == 1, f"count={len(sent_b2)}")

# ── message copy ──────────────────────────────────────────────────────────────
if sent_b2:
    msg_b2 = sent_b2[0]
    print(f"         → Otto: {msg_b2[:120]!r}")
    check("message contains 🚗", "🚗" in msg_b2, f"msg={msg_b2[:60]!r}")
    check("message contains reminder offer",
          "aviso" in msg_b2.lower() or "hora de salir" in msg_b2.lower(),
          f"msg={msg_b2[:80]!r}")
    check("message is not an error",
          "no pude" not in msg_b2.lower() and "error" not in msg_b2.lower(),
          f"msg={msg_b2[:80]!r}")
    has_time = bool(_re.search(r'\d+:\d+', msg_b2)) or "AM" in msg_b2 or "PM" in msg_b2 \
               or " am" in msg_b2.lower() or " pm" in msg_b2.lower()
    check("message contains a time value", has_time, f"msg={msg_b2!r}")

# ── stash shape ───────────────────────────────────────────────────────────────
stash_b2 = get_user_context(TEST_PHONE).get("pending_travel")
check("stash advanced to step 2",
      (stash_b2 or {}).get("step") == "awaiting_reminder_confirmation")

# ── stash content correctness ─────────────────────────────────────────────────
resolved_loc = (stash_b2 or {}).get("resolved_location") or ""
check("resolved_location set", bool(resolved_loc))
# Geocoding CC Andino in Bogotá — result should mention Colombia or Bogotá
check("resolved_location geocoded to Colombia/Bogotá",
      any(tok in resolved_loc for tok in ("Colombia", "Bogot", "Andino", "Cundinamarca")),
      f"resolved_location={resolved_loc!r}")

leave_at_display = (stash_b2 or {}).get("leave_at_display") or ""
check("leave_at_display set", bool(leave_at_display))
# Should look like a formatted time ("4:28 AM", "4:28 am", etc.)
check("leave_at_display is a formatted time",
      bool(_re.search(r'\d+:\d+', leave_at_display)),
      f"leave_at_display={leave_at_display!r}")

duration = (stash_b2 or {}).get("duration_minutes") or 0
check("duration_minutes > 0", duration > 0, f"duration={duration}")
check("duration_minutes sane for intra-Bogotá trip (≤ 120 min)",
      duration <= 120, f"duration={duration}")

# B1 fields must be carried over unchanged
check("event_title carried from B1",
      (stash_b2 or {}).get("event_title") == "Almuerzo de trabajo",
      f"event_title={(stash_b2 or {}).get('event_title')!r}")
check("event_start_iso carried from B1",
      bool((stash_b2 or {}).get("event_start_iso")))


# ─────────────────────────────────────────────────────────────────────────────
# B3: User says "sí" → reminder scheduled in Firestore + confirmation sent
# ─────────────────────────────────────────────────────────────────────────────
print("\n── B3: User confirms reminder → Firestore doc written ───────────────")

sent_b3 = []
with patch("app.handlers.pending_travel_handler.send_whatsapp_message",
           side_effect=lambda phone, msg: sent_b3.append(msg)):
    inbound = make_inbound("sí")
    consumed = handle_pending_travel(inbound, TEST_USER)

check("gate consumed the message", consumed)
check("stash cleared after confirm", get_user_context(TEST_PHONE).get("pending_travel") is None)
check("confirmation message sent", len(sent_b3) >= 1)

if sent_b3:
    msg = sent_b3[0]
    check("confirmation contains 🔔", "🔔" in msg, f"msg={msg[:60]!r}")
    print(f"         → Otto: {msg!r}")

# Check Firestore doc was written
import time; time.sleep(1)  # brief pause for Firestore write
reminder_docs = list(
    db.collection("scheduled_reminders")
    .where("user_phone_number", "==", TEST_PHONE)
    .stream()
)
check("Firestore reminder doc created", len(reminder_docs) >= 1)
if reminder_docs:
    doc_data = reminder_docs[0].to_dict()
    check("type is 'departure'", doc_data.get("type") == "departure")
    check("sent_at is None", doc_data.get("sent_at") is None)
    check("fire_at is set", bool(doc_data.get("fire_at")))
    check("event_title correct", "Almuerzo" in (doc_data.get("event_title") or ""))
    print(f"         → Firestore: fire_at={doc_data.get('fire_at')!r}")


# ─────────────────────────────────────────────────────────────────────────────
# B4: Abort during reminder confirmation
# ─────────────────────────────────────────────────────────────────────────────
print("\n── B4: User aborts reminder offer ───────────────────────────────────")

clear_context()
update_user_context(TEST_PHONE, "pending_travel", {
    "step": "awaiting_reminder_confirmation",
    "event_title": "Test Event",
    "event_start_iso": (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(),
    "resolved_location": "CC Andino, Bogotá",
    "leave_at_display": "2:00 PM",
    "duration_minutes": 30,
})

sent_b4 = []
with patch("app.handlers.pending_travel_handler.send_whatsapp_message",
           side_effect=lambda phone, msg: sent_b4.append(msg)):
    consumed = handle_pending_travel(make_inbound("no"), TEST_USER)

check("gate consumed the message", consumed)
check("abort ack sent", len(sent_b4) == 1)
check("stash cleared", get_user_context(TEST_PHONE).get("pending_travel") is None)
if sent_b4:
    print(f"         → Otto: {sent_b4[0]!r}")


# ─────────────────────────────────────────────────────────────────────────────
# B5: Unknown place name → geocode_not_found copy
# ─────────────────────────────────────────────────────────────────────────────
print("\n── B5: Unknown place → geocode_not_found copy ───────────────────────")

event_no_loc2 = future_event_without_location()
update_user_context(TEST_PHONE, "today_events", [event_no_loc2])
update_user_context(TEST_PHONE, "pending_travel", {
    "step": "awaiting_location",
    "event_title": "Almuerzo de trabajo",
    "event_start_iso": event_no_loc2["start"],
    "event_id": None,
})

sent_b5 = []
with patch("app.handlers.pending_travel_handler.send_whatsapp_message",
           side_effect=lambda phone, msg: sent_b5.append(msg)):
    consumed = handle_pending_travel(make_inbound("xkjsfhgskdhfjkhsdf"), TEST_USER)

check("gate consumed the message", consumed)
check("error message sent", len(sent_b5) == 1)
check("stash cleared", get_user_context(TEST_PHONE).get("pending_travel") is None)
if sent_b5:
    check("message contains 🗺️", "🗺️" in sent_b5[0])
    print(f"         → Otto: {sent_b5[0]!r}")


# ─────────────────────────────────────────────────────────────────────────────
# B6: Abort during location step
# ─────────────────────────────────────────────────────────────────────────────
print("\n── B6: Abort during location step ───────────────────────────────────")

update_user_context(TEST_PHONE, "pending_travel", {
    "step": "awaiting_location",
    "event_title": "Almuerzo de trabajo",
    "event_start_iso": (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(),
    "event_id": None,
})

sent_b6 = []
with patch("app.handlers.pending_travel_handler.send_whatsapp_message",
           side_effect=lambda phone, msg: sent_b6.append(msg)):
    consumed = handle_pending_travel(make_inbound("cancel"), TEST_USER)

check("gate consumed the message", consumed)
check("stash cleared", get_user_context(TEST_PHONE).get("pending_travel") is None)
check("ack sent", len(sent_b6) == 1)
if sent_b6:
    print(f"         → Otto: {sent_b6[0]!r}")


# ─────────────────────────────────────────────────────────────────────────────
# B7: Long message during location step → falls through to pipeline
# ─────────────────────────────────────────────────────────────────────────────
print("\n── B7: Long message drops stash and falls through ───────────────────")

update_user_context(TEST_PHONE, "pending_travel", {
    "step": "awaiting_location",
    "event_title": "Almuerzo de trabajo",
    "event_start_iso": (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(),
    "event_id": None,
})

long_text = "tengo una reunión en otro lado mañana por la mañana temprano"
consumed = handle_pending_travel(make_inbound(long_text), TEST_USER)

check("gate returns False (not consumed)", not consumed)
check("stash cleared", get_user_context(TEST_PHONE).get("pending_travel") is None)


# ─────────────────────────────────────────────────────────────────────────────
# B8: Cron delivery — writes a past-due reminder, verifies cron sends it
# ─────────────────────────────────────────────────────────────────────────────
print("\n── B8: Cron delivers a due departure reminder ───────────────────────")

# Write a reminder with fire_at 2 min ago (clearly due)
past_fire_at = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
doc_id = ScheduledReminderRepository.create(
    user_phone_number=TEST_PHONE,
    reminder_type="departure",
    event_title="Almuerzo de trabajo",
    event_location="CC Andino, Bogotá",
    event_start_iso=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
    fire_at_iso=past_fire_at,
    lang="es",
)
check("Firestore doc created", bool(doc_id))

sent_b8 = []
with patch("app.api.cron_routes.send_whatsapp_message",
           side_effect=lambda phone, msg: sent_b8.append((phone, msg))):
    count = _run_departure_reminders()

check("cron sent 1 reminder", count >= 1, f"count={count}")
check("message sent to correct phone", any(p == TEST_PHONE for p, _ in sent_b8))
if sent_b8:
    msg = sent_b8[0][1]
    check("message contains ⏰", "⏰" in msg)
    check("message contains event title", "Almuerzo" in msg)
    check("message contains location", "Andino" in msg)
    print(f"         → Otto: {msg!r}")

# Verify doc was deleted after delivery
import time; time.sleep(1)
deleted_doc = db.collection("scheduled_reminders").document(doc_id).get()
check("doc deleted after delivery", not deleted_doc.exists)

# Dedup: second cron run should NOT send again
sent_b8_round2 = []
with patch("app.api.cron_routes.send_whatsapp_message",
           side_effect=lambda phone, msg: sent_b8_round2.append((phone, msg))):
    count2 = _run_departure_reminders()

check("second cron run sends 0 (dedup)", all(p != TEST_PHONE for p, _ in sent_b8_round2),
      f"count2={count2}, sent_to_test={sum(1 for p,_ in sent_b8_round2 if p == TEST_PHONE)}")


# ─────────────────────────────────────────────────────────────────────────────
# cleanup
# ─────────────────────────────────────────────────────────────────────────────
print("\n── Cleanup ──────────────────────────────────────────────────────────")
cleanup_firestore_reminders()
clear_context()
print("  Firestore test docs removed, context cleared.")


# ─────────────────────────────────────────────────────────────────────────────
# summary
# ─────────────────────────────────────────────────────────────────────────────
print("\n── Summary ──────────────────────────────────────────────────────────")
passed = sum(1 for _, ok in results if ok)
failed = sum(1 for _, ok in results if not ok)
print(f"  {passed} passed  |  {failed} failed  |  {len(results)} total")

if failed:
    print("\nFailed checks:")
    for label, ok in results:
        if not ok:
            print(f"  {FAIL}  {label}")
    sys.exit(1)
else:
    print(f"\n  \033[92mAll tests passed.\033[0m")
