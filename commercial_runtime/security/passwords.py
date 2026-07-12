"""
Aura FullSuits -- central password-hashing service.

Algorithm: PBKDF2-HMAC-SHA256, per-password random salt, high iteration count.
Stdlib-only (hashlib.pbkdf2_hmac + hmac + secrets) -- no third-party dependency
on either platform, which matters on Android (Chaquopy pure-Python constraint).

Stored format (self-describing so parameters/algorithm can change later
without a mass-invalidation event):

    pbkdf2_sha256$<iterations>$<salt_b64>$<hash_b64>

A legacy (pre-remediation) password is a bare 64-character lowercase hex
string -- the raw output of hashlib.sha256(pwd.encode()).hexdigest(). This
module can recognise and verify that format ONLY for the purpose of one-time
migration (see authenticate_and_maybe_upgrade below); it is never produced by
hash_password().

Extracted verbatim from Action Aura Enterprise's core/security/passwords.py
(the Retail Phase 1 security remediation) -- see docs/migration/risk-register.md R1.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets

ALGORITHM = "pbkdf2_sha256"
DEFAULT_ITERATIONS = 600_000  # OWASP 2023 minimum for PBKDF2-HMAC-SHA256
SALT_BYTES = 16

_MODERN_RE = re.compile(r"^pbkdf2_sha256\$(\d+)\$([A-Za-z0-9+/=]+)\$([A-Za-z0-9+/=]+)$")
_LEGACY_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class PasswordPolicyError(ValueError):
    """Raised when a password fails the minimum documented policy (non-empty)."""


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _ub64(value: str) -> bytes:
    return base64.b64decode(value.encode("ascii"))


def hash_password(password: str, iterations: int = DEFAULT_ITERATIONS) -> str:
    """Hash a plaintext password. Raises PasswordPolicyError for an empty password --
    documented policy: empty passwords are never accepted, hashed, or stored."""
    if not password:
        raise PasswordPolicyError("Password must not be empty.")
    salt = secrets.token_bytes(SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"{ALGORITHM}${iterations}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored_hash: str) -> bool:
    """Verify against a MODERN (pbkdf2_sha256$...) hash only.

    Fails safe: any malformed, legacy, empty, or unrecognised value returns
    False rather than raising. Never throws on attacker-controlled input."""
    if not password or not stored_hash:
        return False
    match = _MODERN_RE.match(stored_hash.strip())
    if not match:
        return False
    try:
        iterations = int(match.group(1))
        salt = _ub64(match.group(2))
        expected = _ub64(match.group(3))
    except Exception:
        return False
    if iterations <= 0:
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(actual, expected)


def is_legacy_sha256_hash(value: str) -> bool:
    """True only for a bare 64-hex-char SHA-256 digest. Any other string
    (including garbage/malformed values) returns False -- callers must not
    treat arbitrary strings as legacy hashes."""
    if not value:
        return False
    return bool(_LEGACY_SHA256_RE.match(value.strip()))


def verify_legacy_sha256(password: str, stored_hash: str) -> bool:
    """Verify a password against a legacy unsalted-SHA-256 hash. Only used by
    the one-time migration path in authenticate_and_maybe_upgrade."""
    if not password or not is_legacy_sha256_hash(stored_hash):
        return False
    actual = hashlib.sha256(password.encode("utf-8")).hexdigest()
    return hmac.compare_digest(actual, stored_hash.strip())


def needs_rehash(stored_hash: str) -> bool:
    """True when the stored value is legacy, malformed/unrecognised, or a
    modern hash whose iteration count is below the current target."""
    if not stored_hash:
        return True
    if is_legacy_sha256_hash(stored_hash):
        return True
    match = _MODERN_RE.match(stored_hash.strip())
    if not match:
        return True
    return int(match.group(1)) < DEFAULT_ITERATIONS


def authenticate_and_maybe_upgrade(password: str, stored_hash: str) -> tuple[bool, str | None]:
    """Single entry point for verifying a login password against whatever is
    currently stored, transparently handling legacy-SHA-256 migration.

    Returns (ok, new_hash):
      - ok=False, new_hash=None                     -> wrong password, nothing to persist
      - ok=True,  new_hash=None                      -> modern hash, up to date, nothing to persist
      - ok=True,  new_hash=<new modern hash string>  -> caller MUST persist new_hash and may
                                                         record a PASSWORD_HASH_UPGRADED audit event
    Never logs or returns the plaintext password.
    """
    if verify_password(password, stored_hash):
        return True, (hash_password(password) if needs_rehash(stored_hash) else None)
    if is_legacy_sha256_hash(stored_hash) and verify_legacy_sha256(password, stored_hash):
        return True, hash_password(password)
    return False, None
