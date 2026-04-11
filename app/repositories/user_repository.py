from datetime import datetime
from typing import Optional, Dict

from app.core.firebase import db


class UserRepository:
    """
    Repository responsible for managing users in Firestore.
    """

    COLLECTION_NAME = "users"

    @staticmethod
    def get_user(user_phone_number: str) -> Optional[Dict]:
        """
        Retrieve a user by phone number.
        """
        doc_ref = db.collection(UserRepository.COLLECTION_NAME).document(user_phone_number)
        doc = doc_ref.get()

        if doc.exists:
            return doc.to_dict()
        return None

    @staticmethod
    def create_or_update_user(user_phone_number: str, data: Dict) -> Dict:
        """
        Create or update a user document.
        """
        doc_ref = db.collection(UserRepository.COLLECTION_NAME).document(user_phone_number)

        # Add timestamps
        data["updated_at"] = datetime.utcnow()

        # If creating for the first time
        if not doc_ref.get().exists:
            data["created_at"] = datetime.utcnow()

        doc_ref.set(data, merge=True)

        return {
            "user_phone_number": user_phone_number,
            "status": "stored"
        }