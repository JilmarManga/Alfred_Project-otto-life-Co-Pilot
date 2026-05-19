import logging
from datetime import datetime, timedelta, timezone

from app.core.firebase import db

logger = logging.getLogger(__name__)

# Personal-reminder store. DELIBERATELY separate from `scheduled_reminders`
# (TravelAgent's departure reminders) — see Hard Rule on reminder isolation.
_COLLECTION = "user_reminders"

_STATUS_SCHEDULED = "scheduled"
_STATUS_AWAITING_FOLLOWUP = "awaiting_followup"


def _parse_iso(raw):
    """Defensive ISO parse (mirrors ScheduledReminderRepository) — naive → UTC."""
    try:
        dt = datetime.fromisoformat(raw)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


class UserReminderRepository:

    @staticmethod
    def create(
        *,
        user_phone_number: str,
        reminder_text: str,
        fire_at_iso: str,
        lang: str,
        tz: str,
    ) -> str:
        """Write a new scheduled reminder. Returns the Firestore document ID."""
        now = datetime.now(timezone.utc).isoformat()
        doc_ref = db.collection(_COLLECTION).document()
        doc_ref.set({
            "user_phone_number": user_phone_number,
            "reminder_text": reminder_text,
            "fire_at": fire_at_iso,
            "lang": lang,
            "tz": tz,
            "status": _STATUS_SCHEDULED,
            "created_at": now,
            "delivered_at": None,
        })
        return doc_ref.id

    @staticmethod
    def list_due_scheduled(now_utc: datetime, horizon_minutes: int = 15) -> list:
        """status==scheduled AND fire_at in [now - 5min, now + horizon]."""
        upper = now_utc + timedelta(minutes=horizon_minutes)
        lower = now_utc - timedelta(minutes=5)
        try:
            results = []
            for doc in db.collection(_COLLECTION).stream():
                data = doc.to_dict()
                if not data or data.get("status") != _STATUS_SCHEDULED:
                    continue
                fire_at = _parse_iso(data.get("fire_at"))
                if fire_at is None:
                    logger.warning("Bad fire_at for user_reminder %s: %r", doc.id, data.get("fire_at"))
                    continue
                if lower <= fire_at <= upper:
                    results.append({"id": doc.id, **data})
            return results
        except Exception as exc:
            logger.exception("UserReminderRepository.list_due_scheduled failed: %s", exc)
            return []

    @staticmethod
    def mark_awaiting_followup(doc_id: str, delivered_at_iso: str) -> None:
        try:
            db.collection(_COLLECTION).document(doc_id).update({
                "status": _STATUS_AWAITING_FOLLOWUP,
                "delivered_at": delivered_at_iso,
            })
        except Exception as exc:
            logger.exception("UserReminderRepository.mark_awaiting_followup failed for %s: %s", doc_id, exc)

    @staticmethod
    def list_awaiting_followup_for_phone(phone: str) -> list:
        try:
            results = []
            for doc in db.collection(_COLLECTION).stream():
                data = doc.to_dict()
                if not data:
                    continue
                if (data.get("status") == _STATUS_AWAITING_FOLLOWUP
                        and data.get("user_phone_number") == phone):
                    results.append({"id": doc.id, **data})
            return results
        except Exception as exc:
            logger.exception("UserReminderRepository.list_awaiting_followup_for_phone failed: %s", exc)
            return []

    @staticmethod
    def list_stale_awaiting_followup(now_utc: datetime, max_age_minutes: int = 10) -> list:
        """awaiting_followup docs whose delivered_at is older than max_age."""
        cutoff = now_utc - timedelta(minutes=max_age_minutes)
        try:
            results = []
            for doc in db.collection(_COLLECTION).stream():
                data = doc.to_dict()
                if not data or data.get("status") != _STATUS_AWAITING_FOLLOWUP:
                    continue
                delivered = _parse_iso(data.get("delivered_at"))
                if delivered is None or delivered < cutoff:
                    results.append({"id": doc.id, **data})
            return results
        except Exception as exc:
            logger.exception("UserReminderRepository.list_stale_awaiting_followup failed: %s", exc)
            return []

    @staticmethod
    def reschedule(doc_id: str, new_fire_at_iso: str) -> None:
        try:
            db.collection(_COLLECTION).document(doc_id).update({
                "fire_at": new_fire_at_iso,
                "status": _STATUS_SCHEDULED,
                "delivered_at": None,
            })
        except Exception as exc:
            logger.exception("UserReminderRepository.reschedule failed for %s: %s", doc_id, exc)

    @staticmethod
    def list_for_phone(phone: str) -> list:
        try:
            results = []
            for doc in db.collection(_COLLECTION).stream():
                data = doc.to_dict()
                if not data or data.get("user_phone_number") != phone:
                    continue
                results.append({"id": doc.id, **data})
            return results
        except Exception as exc:
            logger.exception("UserReminderRepository.list_for_phone failed: %s", exc)
            return []

    @staticmethod
    def get(doc_id: str) -> dict | None:
        try:
            doc = db.collection(_COLLECTION).document(doc_id).get()
            if not doc.exists:
                return None
            return {"id": doc.id, **(doc.to_dict() or {})}
        except Exception as exc:
            logger.exception("UserReminderRepository.get failed for %s: %s", doc_id, exc)
            return None

    @staticmethod
    def delete(doc_id: str) -> None:
        try:
            db.collection(_COLLECTION).document(doc_id).delete()
        except Exception as exc:
            logger.exception("UserReminderRepository.delete failed for %s: %s", doc_id, exc)
