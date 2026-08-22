"""Symmetric encryption for secrets stored in the database (currently
YouTubeConfig.client_secret / refresh_token). Uses Fernet (AES-128-CBC +
HMAC, authenticated) rather than a bare cipher, so tampering is detected,
not just confidentiality protected.

Applied at the model/service boundary (see models.py's EncryptedText type
below), not scattered through main.py -- callers should never see plaintext
vs ciphertext as a manual step.
"""
from __future__ import annotations
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from .config import get_settings


class EncryptionNotConfigured(Exception):
    pass


@lru_cache
def _fernet() -> Fernet:
    key = get_settings().encryption_key
    if not key:
        raise EncryptionNotConfigured(
            "ENCRYPTION_KEY is not set. Generate one with: "
            "python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_secret(plaintext: str | None) -> str | None:
    if plaintext is None or plaintext == "":
        return None
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_secret(ciphertext: str | None) -> str | None:
    if ciphertext is None or ciphertext == "":
        return None
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken:
        # Ciphertext doesn't match the current key -- e.g. ENCRYPTION_KEY was
        # rotated without re-encrypting existing rows, or the value was
        # somehow stored as plaintext before this module existed. Fail
        # loudly rather than silently returning garbage/None, since a
        # caller using this to authenticate against Google's API needs to
        # know the credential is unusable, not just missing.
        raise