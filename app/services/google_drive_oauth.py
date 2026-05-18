"""Google Drive OAuth — fully isolated from the calendar OAuth flow.

This is a deliberate copy of `google_oauth.py` rather than a shared helper.
Calendar OAuth and Drive OAuth must never share a state-token field: the
calendar callback (`oauth_routes._finalize_connection`) writes a
`connected_accounts` slot, so if Drive consent reused
`google_oauth_state_token` a Drive callback could be resolved by the calendar
callback and silently mis-write a calendar account. Drive therefore uses its
own `google_drive_oauth_*` Firestore fields end to end.

Scope: full Drive (`https://www.googleapis.com/auth/drive`) — read + modify
any file the user owns. Works for beta test users; production requires
Google's sensitive-scope verification (tracked separately, not blocking).
"""
import os
import logging
from typing import Optional

from google_auth_oauthlib.flow import Flow

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/drive"]


def _build_flow(state: Optional[str] = None) -> Flow:
    client_id = os.getenv("GOOGLE_OAUTH_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET")
    redirect_uri = os.getenv("GOOGLE_DRIVE_OAUTH_REDIRECT_URI")

    if not client_id:
        raise RuntimeError("GOOGLE_OAUTH_CLIENT_ID not set in environment")
    if not client_secret:
        raise RuntimeError("GOOGLE_OAUTH_CLIENT_SECRET not set in environment")
    if not redirect_uri:
        raise RuntimeError("GOOGLE_DRIVE_OAUTH_REDIRECT_URI not set in environment")

    client_config = {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [redirect_uri],
        }
    }

    return Flow.from_client_config(
        client_config,
        scopes=SCOPES,
        redirect_uri=redirect_uri,
        state=state,
    )


def build_authorize_url(state_token: str) -> tuple[str, Optional[str]]:
    """Build the Drive OAuth consent URL for a given state token.

    Returns (url, code_verifier) — the PKCE verifier must be stored and passed
    back to `exchange_code(code_verifier=...)` (mirrors google_oauth, Hard
    Rule #15).
    """
    flow = _build_flow(state=state_token)
    url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="false",
        # select_account → always show the Google account chooser so the user
        # picks which account to grant Drive to (not the silently signed-in
        # one). consent → still force the consent screen so a refresh_token
        # is always returned.
        prompt="select_account consent",
    )
    return url, flow.code_verifier


def exchange_code(
    code: str,
    state_token: Optional[str] = None,
    code_verifier: Optional[str] = None,
) -> str:
    """Exchange an OAuth code for credentials and return the refresh_token.

    Raises RuntimeError if Google didn't return a refresh_token.
    """
    flow = _build_flow(state=state_token)
    flow.fetch_token(code=code, code_verifier=code_verifier)
    creds = flow.credentials
    if not creds.refresh_token:
        raise RuntimeError("Google Drive OAuth response did not include a refresh_token")
    return creds.refresh_token
