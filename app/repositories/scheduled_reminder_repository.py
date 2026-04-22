import logging
from datetime import datetime, timezone
from typing import Optional

from app.core.firebase import db

logger = logging.getLogger(__name__)

_COLLECTION = "scheduled_reminders"


class ScheduledReminderRepository:

    @staticmethod
    def create(
        *,
        user_phone_number: str,
        reminder_type: str,
        event_title: str,
        event_location: str,
        event_start_iso: str,
        fire_at_iso: str,
        lang: str,
    ) -> str:
        """Write a new reminder doc. Returns the Firestore document ID."""
        now = datetime.now(timezone.utc).isoformat()
        doc_ref = db.collection(_COLLECTION).document()
        doc_ref.set({
            "user_phone_number": user_phone_number,
            "type": reminder_type,
            "event_title": event_title,
            "event_location": event_location,
            "event_start_iso": event_start_iso,
            "fire_at": fire_at_iso,
            "lang": lang,
            "sent_at": None,
            "created_at": now,
        })
        return doc_ref.id

    @staticmethod
    def list_due_within(now_utc: datetime, horizon_minutes: int = 15) -> list:
        """Return unsent reminders whose fire_at is on or before now + horizon.

        Fire window: [now - margin, now + horizon_minutes].
        The lower bound (5 min ago) prevents re-firing stale docs from a long
        outage scenario. On normal 15-min cron cadence this is never triggered.
        """
        from datetime import timedelta
        upper = now_utc + timedelta(minutes=horizon_minutes)
        lower = now_utc - timedelta(minutes=5)

        try:
            docs = (
                db.collection(_COLLECTION)
                .where("sent_at", "==", None)
                .stream()
            )
            results = []
            for doc in docs:
                data = doc.to_dict()
                if not data:
                    continue
                fire_at_raw = data.get("fire_at")
                if not fire_at_raw:
                    continue
                try:
                    fire_at = datetime.fromisoformat(fire_at_raw)
                    if fire_at.tzinfo is None:
                        fire_at = fire_at.replace(tzinfo=timezone.utc)
                    if lower <= fire_at <= upper:
                        results.append({"id": doc.id, **data})
                except (ValueError, TypeError):
                    logger.warning("Bad fire_at for reminder %s: %r", doc.id, fire_at_raw)
            return results
        except Exception as exc:
            logger.exception("ScheduledReminderRepository.list_due_within failed: %s", exc)
            return []

    @staticmethod
    def mark_sent(doc_id: str, sent_at_iso: Optional[str] = None) -> None:
        if sent_at_iso is None:
            sent_at_iso = datetime.now(timezone.utc).isoformat()
        try:
            db.collection(_COLLECTION).document(doc_id).update({"sent_at": sent_at_iso})
        except Exception as exc:
            logger.exception("ScheduledReminderRepository.mark_sent failed for %s: %s", doc_id, exc)
