import logging
from datetime import datetime, timezone
from typing import Optional

from google.cloud.firestore_v1.base_query import FieldFilter

from app.core.firebase import db

logger = logging.getLogger(__name__)

_COLLECTION = "lists"


class ListRepository:
    """Persists user-defined lists.

    One Firestore doc per list; items are stored inline in an `items` array.
    Every read and write is scoped by `user_phone_number` — no cross-user reads.
    """

    @staticmethod
    def get_user_lists(user_phone_number: str) -> list[dict]:
        """Return every list belonging to the user (includes `id` + full data)."""
        try:
            query = (
                db.collection(_COLLECTION)
                .where(filter=FieldFilter("user_phone_number", "==", user_phone_number))
            )
            results = []
            for doc in query.stream():
                data = doc.to_dict() or {}
                results.append({"id": doc.id, **data})
            return results
        except Exception as exc:
            logger.exception(
                "ListRepository.get_user_lists failed for %s: %s",
                user_phone_number,
                exc,
            )
            return []

    @staticmethod
    def find_list_by_name(user_phone_number: str, name: str) -> Optional[dict]:
        """Case-insensitive lookup via `name_lower`. Returns the doc dict or None."""
        if not name:
            return None
        try:
            query = (
                db.collection(_COLLECTION)
                .where(filter=FieldFilter("user_phone_number", "==", user_phone_number))
                .where(filter=FieldFilter("name_lower", "==", name.strip().lower()))
                .limit(1)
            )
            for doc in query.stream():
                data = doc.to_dict() or {}
                return {"id": doc.id, **data}
            return None
        except Exception as exc:
            logger.exception(
                "ListRepository.find_list_by_name failed for %s/%s: %s",
                user_phone_number,
                name,
                exc,
            )
            return None

    @staticmethod
    def count_user_lists(user_phone_number: str) -> int:
        """Count the user's lists — used for the 3-list cap check."""
        try:
            query = (
                db.collection(_COLLECTION)
                .where(filter=FieldFilter("user_phone_number", "==", user_phone_number))
            )
            return sum(1 for _ in query.stream())
        except Exception as exc:
            logger.exception(
                "ListRepository.count_user_lists failed for %s: %s",
                user_phone_number,
                exc,
            )
            return 0

    @staticmethod
    def create_list(user_phone_number: str, name: str) -> Optional[str]:
        """Create an empty list. Returns the Firestore doc ID, or None on failure."""
        try:
            now = datetime.now(timezone.utc).isoformat()
            doc_ref = db.collection(_COLLECTION).document()
            doc_ref.set({
                "user_phone_number": user_phone_number,
                "name": name,
                "name_lower": name.strip().lower(),
                "items": [],
                "created_at": now,
                "updated_at": now,
            })
            return doc_ref.id
        except Exception as exc:
            logger.exception(
                "ListRepository.create_list failed for %s/%s: %s",
                user_phone_number,
                name,
                exc,
            )
            return None

    @staticmethod
    def append_item(list_id: str, item: dict) -> bool:
        """Append an item to the list's `items` array via read-modify-write.

        Safe under Otto's cadence: one WhatsApp message per user at a time.
        The caller (the save skill) has already read the list to run the
        10-min dedup check, so this is not the first read — no extra cost.
        """
        try:
            doc_ref = db.collection(_COLLECTION).document(list_id)
            snap = doc_ref.get()
            if not snap.exists:
                logger.warning("ListRepository.append_item: list %s not found", list_id)
                return False
            data = snap.to_dict() or {}
            items = list(data.get("items") or [])
            items.append(item)
            doc_ref.update({
                "items": items,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            })
            return True
        except Exception as exc:
            logger.exception(
                "ListRepository.append_item failed for %s: %s",
                list_id,
                exc,
            )
            return False

    @staticmethod
    def delete_list(list_id: str) -> bool:
        """Hard-delete the list doc. Returns True on success."""
        try:
            db.collection(_COLLECTION).document(list_id).delete()
            return True
        except Exception as exc:
            logger.exception(
                "ListRepository.delete_list failed for %s: %s",
                list_id,
                exc,
            )
            return False
