"""TOTP-based MFA (Part F). Secrets are encrypted at rest (Fernet, app-level key
derived from OWNER_SECRET_KEY) -- unlike passwords, a TOTP secret must be
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


def encrypt_totp_secret(raw_secret: str, app_secret_key: str) -> str:
    return _fernet(app_secret_key).encrypt(raw_secret.encode("utf-8")).decode("ascii")


def decrypt_totp_secret(encrypted_secret: str, app_secret_key: str) -> str | None:
    try:
        return _fernet(app_secret_key).decrypt(encrypted_secret.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        return None


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
