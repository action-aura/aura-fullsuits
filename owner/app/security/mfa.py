"""TOTP-based MFA (Part F). Secrets are encrypted at rest (Fernet). The key is
derived from OWNER_MFA_ENCRYPTION_KEY when that is set, otherwise (the legacy
and still-default path) from OWNER_SECRET_KEY. Decryption tries the configured
key first, then the legacy OWNER_SECRET_KEY-derived one, and a successful use
re-encrypts under the configured key -- so rotating OWNER_SECRET_KEY after the
dedicated key is in place cannot lock MFA users out (review D SEC-02). Unlike passwords, a TOTP secret must be
recoverable in plaintext to verify a live 6-digit code, so hashing is not an
option; encryption is. Recovery codes ARE hashed (one-way), since they are only
ever compared, never displayed again after issuance."""
from __future__ import annotations

import base64
import hashlib
import secrets

import pyotp
from cryptography.fernet import Fernet, InvalidToken

RECOVERY_CODE_COUNT = 10


def _fernet(secret_key: str) -> Fernet:
    key_bytes = hashlib.sha256(secret_key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(key_bytes))


def generate_totp_secret() -> str:
    return pyotp.random_base32()


def _configured_key(app_secret_key: str, mfa_encryption_key: str | None) -> str:
    return mfa_encryption_key or app_secret_key


def encrypt_totp_secret(raw_secret: str, app_secret_key: str, mfa_encryption_key: str | None = None) -> str:
    return _fernet(_configured_key(app_secret_key, mfa_encryption_key)).encrypt(raw_secret.encode("utf-8")).decode("ascii")


def _try_decrypt(encrypted_secret: str, key_material: str) -> str | None:
    try:
        return _fernet(key_material).decrypt(encrypted_secret.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        return None


def decrypt_totp_secret(encrypted_secret: str, app_secret_key: str, mfa_encryption_key: str | None = None) -> str | None:
    """Configured key first; if a dedicated MFA key is set, also the legacy
    OWNER_SECRET_KEY-derived key (secrets enrolled before the dedicated key)."""
    raw = _try_decrypt(encrypted_secret, _configured_key(app_secret_key, mfa_encryption_key))
    if raw is None and mfa_encryption_key:
        raw = _try_decrypt(encrypted_secret, app_secret_key)
    return raw


def reencrypt_if_legacy(encrypted_secret: str, app_secret_key: str, mfa_encryption_key: str | None) -> str | None:
    """New ciphertext under the dedicated key when the stored one only opens
    with the legacy key; None when there is nothing to do (no dedicated key
    configured, already current, or undecryptable)."""
    if not mfa_encryption_key:
        return None
    if _try_decrypt(encrypted_secret, mfa_encryption_key) is not None:
        return None
    raw = _try_decrypt(encrypted_secret, app_secret_key)
    if raw is None:
        return None
    return encrypt_totp_secret(raw, app_secret_key, mfa_encryption_key)


def provisioning_uri(raw_secret: str, account_email: str, issuer: str = "Aura Owner") -> str:
    return pyotp.totp.TOTP(raw_secret).provisioning_uri(name=account_email, issuer_name=issuer)


def verify_totp_code(raw_secret: str, code: str) -> bool:
    if not code or not raw_secret:
        return False
    try:
        return pyotp.TOTP(raw_secret).verify(code.strip(), valid_window=1)
    except Exception:
        return False


def generate_recovery_codes(count: int = RECOVERY_CODE_COUNT) -> list[str]:
    return ["-".join([secrets.token_hex(2), secrets.token_hex(2)]) for _ in range(count)]


def hash_recovery_code(code: str) -> str:
    return hashlib.sha256(code.strip().lower().encode("utf-8")).hexdigest()
