"""Per-user Drive token access + file resolution, shared by Drive skills.

Skill rules still apply to callers: these helpers do Firestore/Drive I/O only
— no LLM, no WhatsApp, no user-facing strings.
"""
from typing import List, Tuple

from app.services import google_drive
from app.services.token_crypto import decrypt, TokenCryptoError


class DriveNotConnected(Exception):
    """User has no usable Drive refresh token stored."""


def get_drive_refresh_token(user: dict) -> str:
    """Decrypt the per-user Drive refresh token.

    Raises DriveNotConnected when there is no token or it can't be decrypted
    (a corrupt/old-key token is functionally 'not connected' — the caller
    sends a fresh connect link rather than retrying a dead value).
    """
    enc = user.get("google_drive_refresh_token")
    if not enc:
        raise DriveNotConnected("no_drive_token")
    try:
        return decrypt(enc)
    except TokenCryptoError as exc:
        raise DriveNotConnected(f"undecryptable_drive_token: {exc}") from exc


def resolve_file(refresh_token: str, name_ref: str) -> Tuple[str, List[dict]]:
    """Resolve a user-supplied file name to a single Drive file.

    Returns (status, files):
      - ("ok", [file])          exactly one match
      - ("not_found", [])       zero matches
      - ("ambiguous", [files])  more than one match (caller disambiguates)
    """
    ref = (name_ref or "").strip()
    if not ref:
        return "not_found", []
    files = google_drive.search_files(refresh_token, ref)
    if not files:
        return "not_found", []

    # Promote an exact (case-insensitive) name match over `contains` noise so
    # "Ventas" wins even when "Ventas 2025 copy" also exists.
    exact = [f for f in files if (f.get("name") or "").strip().lower() == ref.lower()]
    if len(exact) == 1:
        return "ok", exact
    if len(files) == 1:
        return "ok", files
    return "ambiguous", files
