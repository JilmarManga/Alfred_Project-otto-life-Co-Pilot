"""Deterministic calendar-provider detection from a free-text reply.

Shared by the onboarding provider-choice step and the second-account gate.
No LLM — pure accent-insensitive substring matching, mirroring the approach
used in `pending_event_handler._strip_accents` so iOS autocorrect/accent
variants ("gmaíl", "óutlook") still match.
"""
import unicodedata
from typing import Optional, Literal

Provider = Literal["google", "microsoft"]

# Phrases people use when they mean a Google account.
_GOOGLE_TRIGGERS = {
    "gmail", "google", "correo de google", "cuenta de google",
    "g mail", "googlemail",
}

# Phrases people use when they mean a Microsoft account.
_MICROSOFT_TRIGGERS = {
    "outlook", "hotmail", "microsoft", "office 365", "office365",
    "office", "live.com", "live", "msn", "exchange",
}


def _strip_accents(s: str) -> str:
    """Remove combining marks so 'óutlook' matches 'outlook'."""
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


_GOOGLE_TRIGGERS_NORM = {_strip_accents(t) for t in _GOOGLE_TRIGGERS}
_MICROSOFT_TRIGGERS_NORM = {_strip_accents(t) for t in _MICROSOFT_TRIGGERS}


def detect_provider(text: str) -> Optional[Provider]:
    """Return 'google' | 'microsoft' | None.

    Returns None when the reply is empty, unrecognized, or mentions both
    providers (ambiguous — caller should re-ask). Order-independent.
    """
    norm = _strip_accents((text or "").lower().strip())
    if not norm:
        return None

    has_google = any(t in norm for t in _GOOGLE_TRIGGERS_NORM)
    has_microsoft = any(t in norm for t in _MICROSOFT_TRIGGERS_NORM)

    if has_google and has_microsoft:
        return None  # ambiguous — let the caller re-ask
    if has_google:
        return "google"
    if has_microsoft:
        return "microsoft"
    return None
