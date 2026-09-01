"""Encryption at rest for per-user integration credentials (Phase 4 of the
multi-user migration). Symmetric (Fernet/AES-128-CBC + HMAC), keyed by
`settings.credentials_encryption_key` — a single operator-held key, not
per-user, since the threat model is "don't leave API tokens sitting in
plaintext in Postgres," not key-per-tenant isolation.

Generate a key with:
    uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
"""

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from archaeologist.config import settings


@lru_cache
def _fernet() -> Fernet:
    if not settings.credentials_encryption_key:
        raise RuntimeError(
            "CREDENTIALS_ENCRYPTION_KEY not set — cannot store integration "
            "credentials. Generate one with Fernet.generate_key() and set it in .env."
        )
    return Fernet(settings.credentials_encryption_key.encode())


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise RuntimeError(
            "Stored credential could not be decrypted — CREDENTIALS_ENCRYPTION_KEY "
            "may have changed since it was saved."
        ) from exc
