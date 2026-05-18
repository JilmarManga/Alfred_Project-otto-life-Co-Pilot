"""Microsoft (Azure AD / Entra) OAuth — mirrors `google_oauth.py`.

Same contract as the Google flow:
  build_authorize_url(state_token) -> (url, pkce_blob)
  exchange_code(pkce_blob, auth_response) -> refresh_token

`pkce_blob` is the JSON-serialized MSAL auth-code-flow dict (contains the
PKCE code_verifier, state and nonce). It is stored in Firestore exactly the
way Google's `code_verifier` string is, and passed back at callback time so
MSAL can validate PKCE + state (Hard Rule #15, generalized to Microsoft).

Authority = `common` so both personal Outlook and work/school accounts work.
Phase-1 scope is calendar only; OneDrive/mail scopes are added in later phases.
"""
import json
import logging
import os
from typing import Optional, Tuple

import msal

logger = logging.getLogger(__name__)

# Reserved scopes (openid/profile/offline_access) are added by MSAL itself —
# do NOT list them here or MSAL raises.
SCOPES = ["Calendars.ReadWrite", "User.Read"]

# Graph resource scope used when redeeming the refresh token later.
GRAPH_SCOPES = ["https://graph.microsoft.com/Calendars.ReadWrite"]


def _authority() -> str:
    tenant = os.getenv("MICROSOFT_OAUTH_TENANT", "common")
    return f"https://login.microsoftonline.com/{tenant}"


def _redirect_uri() -> str:
    uri = os.getenv("MICROSOFT_OAUTH_REDIRECT_URI")
    if not uri:
        raise RuntimeError("MICROSOFT_OAUTH_REDIRECT_URI not set in environment")
    return uri


def _build_app() -> msal.ConfidentialClientApplication:
    client_id = os.getenv("MICROSOFT_OAUTH_CLIENT_ID")
    client_secret = os.getenv("MICROSOFT_OAUTH_CLIENT_SECRET")
    if not client_id:
        raise RuntimeError("MICROSOFT_OAUTH_CLIENT_ID not set in environment")
    if not client_secret:
        raise RuntimeError("MICROSOFT_OAUTH_CLIENT_SECRET not set in environment")
    return msal.ConfidentialClientApplication(
        client_id,
        client_credential=client_secret,
        authority=_authority(),
    )


def build_authorize_url(state_token: str) -> Tuple[str, Optional[str]]:
    """Build the Microsoft consent URL for a given opaque state token.

    Returns (url, pkce_blob). `pkce_blob` (JSON string) MUST be stored and
    passed back to exchange_code() — it carries the PKCE verifier + state
    MSAL needs to validate the callback.
    """
    app = _build_app()
    flow = app.initiate_auth_code_flow(
        SCOPES,
        redirect_uri=_redirect_uri(),
        state=state_token,
        prompt="select_account",
    )
    if "auth_uri" not in flow:
        raise RuntimeError("MSAL did not return an auth_uri")
    return flow["auth_uri"], json.dumps(flow)


def exchange_code(pkce_blob: Optional[str], auth_response: dict) -> str:
    """Redeem the callback for a refresh token.

    `pkce_blob` is the JSON string returned by build_authorize_url().
    `auth_response` is the callback query params dict (must contain code+state).
    Raises RuntimeError if Microsoft didn't return a refresh_token.
    """
    if not pkce_blob:
        raise RuntimeError("Missing Microsoft auth-code flow (PKCE) blob")
    flow = json.loads(pkce_blob)
    app = _build_app()
    result = app.acquire_token_by_auth_code_flow(flow, auth_response)

    if "refresh_token" not in result:
        # Surface a clean reason in logs only — never to the user.
        err = result.get("error")
        desc = result.get("error_description", "")
        raise RuntimeError(
            f"Microsoft OAuth response did not include a refresh_token "
            f"(error={err}: {desc[:200]})"
        )
    return result["refresh_token"]
