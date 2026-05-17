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
        *,
        provider: str = "google",
        slot: str = "primary",
    ) -> None:
        """Store an opaque single-use OAuth state token with expiry and PKCE
        material. The state field name stays `google_oauth_*` for both
        providers (it is the indexed lookup key — opaque, provider-neutral).
        `provider` ('google'|'microsoft') and `slot` ('primary'|'secondary')
        tell the callback which connected_accounts slot to write.

        For Google `code_verifier` is the PKCE verifier string; for Microsoft
        it is the JSON auth-code-flow blob — stored per-provider so the two
        flows never collide.
        """
        data = {
            "google_oauth_state_token": state_token,
            "google_oauth_state_expires_at": expires_at,
            "oauth_pending_provider": provider,
            "oauth_pending_slot": slot,
        }
        if code_verifier:
            if provider == "microsoft":
                data["microsoft_oauth_flow"] = code_verifier
            else:
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
                "oauth_pending_provider": None,
                "oauth_pending_slot": None,
                "microsoft_oauth_flow": None,
            },
        )

    # --- Multi-provider connected accounts (max 2, any provider mix) ---

    MAX_CONNECTED_ACCOUNTS = 2

    @staticmethod
    def _seed_legacy_account(data: Dict, now: datetime) -> List[Dict]:
        """Legacy-compat (no migration): a pre-existing Google-only user has
        flat `google_calendar_refresh_token` but no `connected_accounts`.
        Treat that token as the existing primary so adding a second account
        never drops it."""
        accounts = list(data.get("connected_accounts") or [])
        if not accounts and data.get("google_calendar_refresh_token"):
            accounts.append({
                "provider": "google",
                "email": None,
                "refresh_token": data["google_calendar_refresh_token"],
                "is_primary": True,
                "reminders_enabled": data.get("calendar_reminders_enabled") is not False,
                "created_at": data.get("created_at") or now,
                "updated_at": now,
            })
        return accounts

    @staticmethod
    def save_connected_account(
        user_phone_number: str,
        *,
        provider: str,
        encrypted_refresh_token: str,
        slot: str = "primary",
        email: Optional[str] = None,
    ) -> None:
        """Write/replace a connected calendar account in the given slot.

        Slot 'primary' = index 0, 'secondary' = index 1. Re-linking an
        existing slot replaces it. Cap is MAX_CONNECTED_ACCOUNTS. When the
        primary is Google, the legacy `google_calendar_*` fields are kept in
        sync so untouched Google-only consumers keep working during the
        transition. `has_connected_calendar` is the new indexed query field.
        """
        now = datetime.utcnow()
        doc_ref = db.collection(UserRepository.COLLECTION_NAME).document(user_phone_number)
        snapshot = doc_ref.get()
        data = (snapshot.to_dict() or {}) if snapshot.exists else {}
        accounts = UserRepository._seed_legacy_account(data, now)

        entry = {
            "provider": provider,
            "email": email,
            "refresh_token": encrypted_refresh_token,
            "is_primary": slot == "primary",
            "reminders_enabled": True,
            "created_at": now,
            "updated_at": now,
        }

        if slot == "primary":
            if accounts:
                entry["created_at"] = accounts[0].get("created_at") or now
                accounts[0] = entry
            else:
                accounts.append(entry)
        else:  # secondary
            if not accounts:
                # No primary yet — promote this to primary defensively.
                entry["is_primary"] = True
                accounts.append(entry)
            elif len(accounts) >= 2:
                entry["created_at"] = accounts[1].get("created_at") or now
                accounts[1] = entry
            else:
                accounts.append(entry)

        accounts = accounts[: UserRepository.MAX_CONNECTED_ACCOUNTS]

        update: Dict = {
            "connected_accounts": accounts,
            "has_connected_calendar": True,
        }
        primary = accounts[0]
        if primary.get("provider") == "google":
            update["google_calendar_refresh_token"] = primary["refresh_token"]
            update["google_calendar_connected"] = True
        UserRepository.create_or_update_user(user_phone_number, update)

    @staticmethod
    def count_connected_accounts(user: Dict) -> int:
        """Number of connected accounts, honoring the legacy-compat shim."""
        accounts = user.get("connected_accounts")
        if accounts:
            return len(accounts)
        return 1 if user.get("google_calendar_refresh_token") else 0

    @staticmethod
    def clear_connected_account(user_phone_number: str, provider: str) -> None:
        """Remove the first connected account matching `provider` (used by the
        reconnect flow when a token is rejected). Recomputes the legacy
        mirror + `has_connected_calendar`."""
        now = datetime.utcnow()
        doc_ref = db.collection(UserRepository.COLLECTION_NAME).document(user_phone_number)
        snapshot = doc_ref.get()
        data = (snapshot.to_dict() or {}) if snapshot.exists else {}
        accounts = UserRepository._seed_legacy_account(data, now)

        remaining = [a for a in accounts if a.get("provider") != provider]
        if remaining:
            remaining[0]["is_primary"] = True
            for a in remaining[1:]:
                a["is_primary"] = False

        update: Dict = {
            "connected_accounts": remaining,
            "has_connected_calendar": bool(remaining),
        }
        if not remaining or remaining[0].get("provider") != "google":
            update["google_calendar_refresh_token"] = None
            update["google_calendar_connected"] = False
        else:
            update["google_calendar_refresh_token"] = remaining[0]["refresh_token"]
            update["google_calendar_connected"] = True
        UserRepository.create_or_update_user(user_phone_number, update)

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
    def clear_calendar_credentials(user_phone_number: str) -> None:
        """Wipe the stored refresh token after Google rejects it. Paired with
        the reconnect flow so a stale token can never be retried."""
        UserRepository.create_or_update_user(
            user_phone_number,
            {
                "google_calendar_refresh_token": None,
                "google_calendar_connected": False,
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
    def _has_any_calendar(data: Dict) -> bool:
        """True if the user has at least one connected calendar account,
        across providers. Honors the legacy Google-only shim so pre-existing
        users keep working with zero migration."""
        if data.get("connected_accounts"):
            return True
        return bool(data.get("google_calendar_refresh_token"))

    @staticmethod
    def list_users_for_reminders() -> List[Dict]:
        """
        Users with at least one connected calendar (Google or Microsoft) who
        haven't opted out of 1-hour reminders. `calendar_reminders_enabled` is
        treated as True by default — only an explicit False disables them.

        Streams the collection and filters in Python (same pattern as
        `list_pending_oauth_followups`): provider-agnostic membership can't be
        expressed as a single indexed Firestore filter, and beta volume is low.
        """
        query = db.collection(UserRepository.COLLECTION_NAME)
        results: List[Dict] = []
        for doc in query.stream():
            data = doc.to_dict() or {}
            if data.get("calendar_reminders_enabled") is False:
                continue
            if not UserRepository._has_any_calendar(data):
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
        Users with at least one connected calendar (Google or Microsoft) who
        haven't opted out of reminders. Explicit `calendar_reminders_enabled
        =False` disables both 1-hour reminders and the morning brief.
        """
        query = db.collection(UserRepository.COLLECTION_NAME)
        results: List[Dict] = []
        for doc in query.stream():
            data = doc.to_dict() or {}
            if data.get("calendar_reminders_enabled") is False:
                continue
            if not UserRepository._has_any_calendar(data):
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