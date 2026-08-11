"""Aura Owner -- staff password hashing.

Argon2id via argon2-cffi (ADR-2). Deliberately NOT the PBKDF2 module used by
Retail/Clinic (commercial_runtime/security/passwords.py) -- that module exists
for Android/Chaquopy's stdlib-only constraint, which does not apply to this
server-only application, and Owner must not import product runtime code anyway.
"""
from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHash, VerifyMismatchError
from flask_babel import gettext as _

_hasher = PasswordHasher()

MIN_PASSWORD_LENGTH = 12


class PasswordPolicyError(ValueError):
    """Raised when a password fails the minimum documented staff password policy."""


def validate_password_policy(password: str) -> None:
    if not password or len(password) < MIN_PASSWORD_LENGTH:
        # %(min_length)s (not an f-string) so pybabel's extractor can see a
        # real, static msgid with a named placeholder -- an f-string
        # argument to _() cannot be extracted at all (Milestone 8's own
        # catalog-completeness requirement depends on this).
        raise PasswordPolicyError(_("Password must be at least %(min_length)s characters.") % {"min_length": MIN_PASSWORD_LENGTH})
    classes = sum(
        [
            any(c.islower() for c in password),
            any(c.isupper() for c in password),
            any(c.isdigit() for c in password),
            any(not c.isalnum() for c in password),
        ]
    )
    if classes < 3:
        raise PasswordPolicyError(_("Password must mix at least 3 of: lowercase, uppercase, digit, symbol."))


def hash_password(password: str) -> str:
    validate_password_policy(password)
    return _hasher.hash(password)


def verify_password(password: str, stored_hash: str) -> bool:
    if not password or not stored_hash:
        return False
    try:
        return _hasher.verify(stored_hash, password)
    except (VerifyMismatchError, InvalidHash, ValueError):
        return False


def needs_rehash(stored_hash: str) -> bool:
    try:
        return _hasher.check_needs_rehash(stored_hash)
    except InvalidHash:
        return True
