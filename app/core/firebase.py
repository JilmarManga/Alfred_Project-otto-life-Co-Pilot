import base64
import os
import tempfile
from pathlib import Path

import firebase_admin
from firebase_admin import credentials, firestore


def _get_firebase_credentials() -> credentials.Certificate:
    # 1. Base64-encoded JSON via env var (production / Railway)
    b64 = os.getenv("FIREBASE_CREDENTIALS_BASE64")
    if b64:
        json_bytes = base64.b64decode(b64)
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
        tmp.write(json_bytes)
        tmp.flush()
        tmp.close()
        return credentials.Certificate(tmp.name)

    # 2. File path via env var
    env_path = os.getenv("FIREBASE_CREDENTIALS_PATH")
    if env_path and Path(env_path).exists():
        return credentials.Certificate(env_path)

    # 3. Fallback for local development
    project_root = Path(__file__).resolve().parents[2]
    local_path = project_root / "credentials" / "firebase-service-account.json"
    if local_path.exists():
        return credentials.Certificate(str(local_path))

    raise ValueError(
        "Firebase credentials not found. "
        "Set FIREBASE_CREDENTIALS_BASE64, FIREBASE_CREDENTIALS_PATH, "
        "or place the file at 'credentials/firebase-service-account.json'"
    )


if not firebase_admin._apps:
    cred = _get_firebase_credentials()
    firebase_admin.initialize_app(cred)

db = firestore.client()