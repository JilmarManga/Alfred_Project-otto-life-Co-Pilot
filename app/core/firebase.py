import os
from pathlib import Path

import firebase_admin
from firebase_admin import credentials, firestore


def get_firebase_credentials_path() -> str:
    # 1. Try environment variable first
    env_path = os.getenv("FIREBASE_CREDENTIALS_PATH")
    if env_path:
        return env_path

    # 2. Fallback for local development
    project_root = Path(__file__).resolve().parents[2]
    local_path = project_root / "credentials" / "firebase-service-account.json"

    if local_path.exists():
        return str(local_path)

    raise ValueError(
        "Firebase credentials not found. "
        "Set FIREBASE_CREDENTIALS_PATH or place the file at "
        "'credentials/firebase-service-account.json'"
    )


if not firebase_admin._apps:
    cred_path = get_firebase_credentials_path()
    cred = credentials.Certificate(cred_path)
    firebase_admin.initialize_app(cred)

db = firestore.client()