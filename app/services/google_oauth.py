import os
import logging
from typing import Optional

from google_auth_oauthlib.flow import Flow

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]


def _build_flow(state: Optional[str] = None) -> Flow:
    client_id = os.getenv("GOOGLE_OAUTH_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET")
    redirect_uri = os.getenv("GOOGLE_OAUTH_REDIRECT_URI")

    if not client_id:
        raise RuntimeError("GOOGLE_OAUTH_CLIENT_ID not set in environment")
    if not client_secret:
        raise RuntimeError("GOOGLE_OAUTH_CLIENT_SECRET not set in environment")
    if not redirect_uri:
        raise RuntimeError("GOOGLE_OAUTH_REDIRECT_URI not set in environment")

    client_config = {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [redirect_uri],
        }
    }

    flow = Flow.from_client_config(
        client_config,
        scopes=SCOPES,
        redirect_uri=redirect_uri,
        state=state,
    )
    return flow


def build_authorize_url(state_token: str) -> str:
    """
    Build the Google OAuth consent URL for a given state token.
    Forces prompt=consent so Google always returns a refresh_token,
    even for users who've previously authorized the app.
    """
    flow = _build_flow(state=state_token)
    url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    return url


def exchange_code(code: str, state_token: Optional[str] = None) -> str:
    """
    Exchange an OAuth code for credentials and return the refresh_token.
    Raises RuntimeError if Google didn't return a refresh_token.
    """
    flow = _build_flow(state=state_token)
    flow.fetch_token(code=code)
    creds = flow.credentials
    if not creds.refresh_token:
        raise RuntimeError("Google OAuth response did not include a refresh_token")
    return creds.refresh_token
