"""
Root conftest — stubs out Firebase/Firestore so tests can import any module
without needing FIREBASE_CREDENTIALS_JSON or the firebase_admin package.
"""
import sys
from unittest.mock import MagicMock

# Stub firebase_admin before any module imports it
_firebase_stub = MagicMock()
sys.modules.setdefault("firebase_admin", _firebase_stub)
sys.modules.setdefault("firebase_admin.credentials", _firebase_stub.credentials)
sys.modules.setdefault("firebase_admin.firestore", _firebase_stub.firestore)

# Provide a consistent fake db object used by all repository imports
_db_mock = MagicMock()
_firebase_stub.firestore.client.return_value = _db_mock

import app.core.firebase as _firebase_module  # noqa: E402
_firebase_module.db = _db_mock
