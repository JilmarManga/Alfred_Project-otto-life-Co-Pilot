from datetime import datetime, timedelta
from typing import Optional, Dict, List

from google.cloud.firestore_v1.base_query import FieldFilter

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

    @staticmethod
    def set_onboarding_state(user_phone_number: str, state: str) -> None:
        """Update the onboarding state machine marker for a user."""
        UserRepository.create_or_update_user(
            user_phone_number,
            {
                "onboarding_state": state,
                "onboarding_completed": state == "completed",
            },
        )

    @staticmethod
    def set_oauth_state_token(
        user_phone_number: str, state_token: str, expires_at: datetime,
        code_verifier: str = None,
    ) -> None:
        """Store an opaque single-use OAuth state token with expiry and PKCE verifier."""
        data = {
            "google_oauth_state_token": state_token,
            "google_oauth_state_expires_at": expires_at,
        }
        if code_verifier:
            data["google_oauth_code_verifier"] = code_verifier
        UserRepository.create_or_update_user(user_phone_number, data)

    @staticmethod
    def get_user_by_oauth_state(state_token: str) -> Optional[Dict]:
        """
        Look up a user by their current OAuth state token. Returns the user
        dict with `phone` merged in, or None if not found / expired.
        """
        query = (
            db.collection(UserRepository.COLLECTION_NAME)
            .where(filter=FieldFilter("google_oauth_state_token", "==", state_token))
            .limit(1)
        )
        for doc in query.stream():
            data = doc.to_dict() or {}
            expires_at = data.get("google_oauth_state_expires_at")
            if expires_at and hasattr(expires_at, "timestamp"):
                if datetime.utcnow().timestamp() > expires_at.timestamp():
                    return None
            data["phone"] = doc.id
            return data
        return None

    @staticmethod
    def clear_oauth_state(user_phone_number: str) -> None:
        """Wipe the one-time OAuth state fields after a successful callback."""
        UserRepository.create_or_update_user(
            user_phone_number,
            {
                "google_oauth_state_token": None,
                "google_oauth_state_expires_at": None,
            },
        )

    @staticmethod
    def save_calendar_credentials(
        user_phone_number: str, encrypted_refresh_token: str
    ) -> None:
        """Persist the encrypted per-user Google Calendar refresh token."""
        UserRepository.create_or_update_user(
            user_phone_number,
            {
                "google_calendar_refresh_token": encrypted_refresh_token,
                "google_calendar_connected": True,
            },
        )

    @staticmethod
    def mark_oauth_link_sent(user_phone_number: str, followup_delay_hours: int = 3) -> None:
        """Record when the OAuth link was sent and schedule the 3h followup."""
        now = datetime.utcnow()
        UserRepository.create_or_update_user(
            user_phone_number,
            {
                "oauth_link_sent_at": now,
                "oauth_followup_due_at": now + timedelta(hours=followup_delay_hours),
                "oauth_followup_sent_at": None,
            },
        )

    @staticmethod
    def mark_oauth_followup_sent(user_phone_number: str) -> None:
        """Mark the followup as sent so we never send it twice."""
        UserRepository.create_or_update_user(
            user_phone_number,
            {
                "oauth_followup_sent_at": datetime.utcnow(),
                "oauth_followup_due_at": None,
            },
        )

    @staticmethod
    def list_pending_oauth_followups(now: Optional[datetime] = None) -> List[Dict]:
        """
        Users who were sent an OAuth link, haven't connected yet, and whose
        3h followup is due. Applied Python-side filtering keeps the Firestore
        query simple (no composite index needed for V1.0.0 volume).
        """
        now = now or datetime.utcnow()
        query = (
            db.collection(UserRepository.COLLECTION_NAME)
            .where(filter=FieldFilter("onboarding_state", "==", "oauth_pending"))
        )
        results: List[Dict] = []
        for doc in query.stream():
            data = doc.to_dict() or {}
            if data.get("oauth_followup_sent_at") is not None:
                continue
            due = data.get("oauth_followup_due_at")
            if due is None:
                continue
            if hasattr(due, "timestamp") and due.timestamp() > now.timestamp():
                continue
            data["phone"] = doc.id
            results.append(data)
        return results

    @staticmethod
    def list_pending_location_retries(now: Optional[datetime] = None) -> List[Dict]:
        """Users whose location_resolver hit an api_error during onboarding."""
        query = (
            db.collection(UserRepository.COLLECTION_NAME)
            .where(filter=FieldFilter("location_resolution_status", "==", "pending_retry"))
        )
        results: List[Dict] = []
        for doc in query.stream():
            data = doc.to_dict() or {}
            data["phone"] = doc.id
            results.append(data)
        return results

    @staticmethod
    def save_resolved_location(
        user_phone_number: str,
        *,
        location: str,
        latitude: float,
        longitude: float,
        timezone: str,
    ) -> None:
        """Persist a fully-resolved location after geocoding succeeds."""
        UserRepository.create_or_update_user(
            user_phone_number,
            {
                "location": location,
                "latitude": latitude,
                "longitude": longitude,
                "timezone": timezone,
                "location_resolution_status": "resolved",
            },
        )

    # --- Calendar reminder helpers (1-hour reminder cron) ---

    @staticmethod
    def set_calendar_reminders_enabled(user_phone_number: str, enabled: bool) -> None:
        """Toggle the 1-hour reminder feature for a user."""
        UserRepository.create_or_update_user(
            user_phone_number,
            {"calendar_reminders_enabled": enabled},
        )

    @staticmethod
    def list_users_for_reminders() -> List[Dict]:
        """
        Users who have connected Google Calendar and haven't opted out of
        1-hour reminders. `calendar_reminders_enabled` is treated as True
        by default — only an explicit False disables reminders.
        """
        query = (
            db.collection(UserRepository.COLLECTION_NAME)
            .where(filter=FieldFilter("google_calendar_connected", "==", True))
        )
        results: List[Dict] = []
        for doc in query.stream():
            data = doc.to_dict() or {}
            if data.get("calendar_reminders_enabled") is False:
                continue
            if not data.get("google_calendar_refresh_token"):
                continue
            data["phone"] = doc.id
            results.append(data)
        return results

    @staticmethod
    def add_notified_event(user_phone_number: str, dedup_key: str, max_entries: int = 100) -> None:
        """
        Append a "{eventId}:{YYYY-MM-DD}" entry to notified_event_ids so the
        same reminder can't fire twice. Trims the list to the last
        `max_entries` to keep the doc bounded.
        """
        doc_ref = db.collection(UserRepository.COLLECTION_NAME).document(user_phone_number)
        snapshot = doc_ref.get()
        current: List[str] = []
        if snapshot.exists:
            current = (snapshot.to_dict() or {}).get("notified_event_ids") or []
        if dedup_key in current:
            return
        current.append(dedup_key)
        if len(current) > max_entries:
            current = current[-max_entries:]
        doc_ref.set(
            {
                "notified_event_ids": current,
                "updated_at": datetime.utcnow(),
            },
            merge=True,
        )

    # --- Morning brief helpers ---

    @staticmethod
    def list_users_for_morning_brief() -> List[Dict]:
        """
        Users who have connected Google Calendar and haven't opted out of
        reminders. Explicit `calendar_reminders_enabled=False` disables both
        1-hour reminders and the morning brief.
        """
        query = (
            db.collection(UserRepository.COLLECTION_NAME)
            .where(filter=FieldFilter("google_calendar_connected", "==", True))
        )
        results: List[Dict] = []
        for doc in query.stream():
            data = doc.to_dict() or {}
            if data.get("calendar_reminders_enabled") is False:
                continue
            if not data.get("google_calendar_refresh_token"):
                continue
            data["phone"] = doc.id
            results.append(data)
        return results

    @staticmethod
    def mark_morning_brief_sent(user_phone_number: str, local_date_iso: str) -> None:
        """Persist the local YYYY-MM-DD on which the morning brief was sent for dedup."""
        UserRepository.create_or_update_user(
            user_phone_number,
            {"morning_brief_sent_date": local_date_iso},
        )

    # --- Operator broadcast helpers ---

    @staticmethod
    def list_onboarded_users() -> List[Dict]:
        """
        Users who have finished onboarding. Honors both the new state machine
        (`onboarding_state == "completed"`) and the legacy flag
        (`onboarding_completed=True` with no `onboarding_state` field) per
        CLAUDE.md "Legacy compat" note. Used by the operator broadcast endpoint.

        Beta-scale: full collection scan. When user count grows, replace with a
        Firestore-side `where("onboarding_state", "==", "completed")` query plus
        a separate legacy-flag query, unioned in Python.
        """
        results: List[Dict] = []
        for doc in db.collection(UserRepository.COLLECTION_NAME).stream():
            data = doc.to_dict() or {}
            is_completed = (
                data.get("onboarding_state") == "completed"
                or (data.get("onboarding_completed") is True and not data.get("onboarding_state"))
            )
            if not is_completed:
                continue
            data["phone"] = doc.id
            results.append(data)
        return results