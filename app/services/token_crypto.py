import os

from cryptography.fernet import Fernet, InvalidToken


class TokenCryptoError(RuntimeError):
    """Raised when encryption/decryption fails or the key is misconfigured."""


def _get_fernet() -> Fernet:
    key = os.getenv("CALENDAR_TOKEN_ENCRYPTION_KEY")
    if not key:
        raise TokenCryptoError(
            "CALENDAR_TOKEN_ENCRYPTION_KEY is not set. "
            "Generate one with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except ValueError as exc:
        raise TokenCryptoError(f"Invalid CALENDAR_TOKEN_ENCRYPTION_KEY: {exc}") from exc


def encrypt(plain: str) -> str:
    return _get_fernet().encrypt(plain.encode()).decode()


def decrypt(cipher: str) -> str:
    try:
        return _get_fernet().decrypt(cipher.encode()).decode()
    except InvalidToken as exc:
        raise TokenCryptoError("Failed to decrypt token — wrong key or corrupted data") from exc
