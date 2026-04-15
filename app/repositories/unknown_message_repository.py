from datetime import datetime
from typing import Any, Dict, List, Optional

from app.core.firebase import db


class UnknownMessageRepository:
    """
    Logs messages Otto couldn't fulfill, for product research.

    Never filter, clean, or normalize raw_message before saving —
    the raw input IS the research value.
    """

    COLLECTION_NAME = "unknown_messages"

    @staticmethod
    def log(
        user_phone_number: str,
        raw_message: str,
        category: str,
        *,
        language: Optional[str] = None,
        onboarding_state: Optional[str] = None,
        parsed_signals: Optional[List[str]] = None,
        routed_to: Optional[str] = None,
        user_context: Optional[Dict[str, Any]] = None,
    ) -> str:
        doc_ref = db.collection(UnknownMessageRepository.COLLECTION_NAME).document()
        doc_ref.set(
            {
                "user_phone_number": user_phone_number,
                "raw_message": raw_message,
                "category": category,
                "language": language,
                "onboarding_state": onboarding_state,
                "parsed_signals": parsed_signals or [],
                "routed_to": routed_to,
                "user_context": user_context or {},
                "created_at": datetime.utcnow(),
            }
        )
        return doc_ref.id
