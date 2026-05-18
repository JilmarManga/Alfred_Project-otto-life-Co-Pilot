"""One-time backfill: migrate legacy Google-only users to the multi-provider
`connected_accounts` model.

Idempotent and safe to re-run. For every user that has the legacy flat
`google_calendar_refresh_token` but no `connected_accounts`, it writes a
single primary Google account entry and sets `has_connected_calendar=True`.
Users already on the new model are skipped.

Builder-facing utility — not part of the request pipeline. The runtime keeps
working without this thanks to the legacy-compat shim in
`calendar_accounts.iter_calendar_accounts`; this just makes the new model
canonical so the `has_connected_calendar` field is populated.

Usage:
    python3 app/scripts/backfill_connected_accounts.py
"""
from datetime import datetime

from app.core.firebase import db

COLLECTION = "users"


def main() -> None:
    now = datetime.utcnow()
    migrated = skipped = 0

    for doc in db.collection(COLLECTION).stream():
        data = doc.to_dict() or {}

        if data.get("connected_accounts"):
            skipped += 1
            continue

        legacy = data.get("google_calendar_refresh_token")
        if not legacy:
            skipped += 1
            continue

        account = {
            "provider": "google",
            "email": None,
            "refresh_token": legacy,
            "is_primary": True,
            "reminders_enabled": data.get("calendar_reminders_enabled") is not False,
            "created_at": data.get("created_at") or now,
            "updated_at": now,
        }
        db.collection(COLLECTION).document(doc.id).set(
            {
                "connected_accounts": [account],
                "has_connected_calendar": True,
                "google_calendar_connected": True,
                "updated_at": now,
            },
            merge=True,
        )
        migrated += 1
        print(f"  migrated {doc.id}")

    print(f"\n✅ Backfill complete — migrated={migrated} skipped={skipped}")


if __name__ == "__main__":
    main()
