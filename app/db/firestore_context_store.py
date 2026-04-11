import logging
from app.core.firebase import db

logger = logging.getLogger(__name__)

COLLECTION_NAME = "user_context"


def get_user_context(user_id: str) -> dict:
    """Fetch user context from Firestore. Returns {} if not found."""
    try:
        doc = db.collection(COLLECTION_NAME).document(user_id).get()
        return doc.to_dict() or {}
    except Exception as e:
        logger.warning("Failed to get user context for %s: %s", user_id, e)
        return {}


def update_user_context(user_id: str, key: str, value) -> None:
    """Write a single key into the user's context document (merge)."""
    try:
        db.collection(COLLECTION_NAME).document(user_id).set(
            {key: value}, merge=True
        )
    except Exception as e:
        logger.warning("Failed to update user context for %s: %s", user_id, e)
