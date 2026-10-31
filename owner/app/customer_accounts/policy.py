"""Customer password policy (the staff policy in app/security/passwords.py is
unchanged and separate).

Rules, each with a stable machine code the website turns into words:
  too_short        fewer than 10 characters
  too_long         more than 128 (an Argon2 DoS bound, not a style rule)
  too_common       in the common-password list
  contains_personal  equals or contains the account email (or its local part,
                   4+ characters), the legal name or the trade name (4+ characters,
                   compared with spaces and punctuation removed)
"""
from __future__ import annotations

import re

from app.customer_accounts.common_passwords import COMMON_PASSWORDS

MIN_LENGTH = 10
MAX_LENGTH = 128
_MIN_PERSONAL_LEN = 4


class PasswordPolicyError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _squash(text: str) -> str:
    return re.sub(r"[\W_]+", "", (text or "").casefold())


def validate(password: str, *, email: str = "", legal_name: str = "", trade_name: str = "") -> None:
    password = password or ""
    if len(password) < MIN_LENGTH:
        raise PasswordPolicyError("too_short")
    if len(password) > MAX_LENGTH:
        raise PasswordPolicyError("too_long")
    lowered = password.casefold().replace(" ", "")
    if lowered in COMMON_PASSWORDS:
        raise PasswordPolicyError("too_common")
    squashed = _squash(password)
    personal = []
    if email:
        local = email.split("@", 1)[0]
        personal += [_squash(email), _squash(local)]
    personal += [_squash(legal_name), _squash(trade_name)]
    for item in personal:
        if len(item) >= _MIN_PERSONAL_LEN and item in squashed:
            raise PasswordPolicyError("contains_personal")
