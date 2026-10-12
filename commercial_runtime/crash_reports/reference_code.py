"""Crash reports -- the short reference code a person can read out or type.

A shopkeeper who hits a problem should be able to say "my code is K7PX2M" on a
phone or in a WhatsApp message, and Action Aura staff should be able to find
that exact report in Owner. So the code is DERIVED from the report's
fingerprint (the 32-hex id both sides already store) instead of being stored
or invented: the till and Owner each compute it, and cannot disagree about
which report a code means.

  * 6 characters from a 32-symbol alphabet = 30 bits = the first 30 bits of
    the fingerprint. About a billion codes: a collision between two reports a
    support person is choosing between is vanishingly unlikely, and a code
    that does collide is still only ever a SEARCH hint (staff land on a short
    list), never an identity.
  * The alphabet drops 0, O, 1 and I -- the four characters people misread
    over the phone or off a screen. (L stays; it is not confused with 1 in a
    capital-letter sans-serif code.)
  * Uppercase only, so reading it aloud has no "capital or small" question.

Owner has a copy of this file's logic in owner/app/crash_reports/
reference_code.py (Owner ships separately and cannot import this package);
owner/tests/test_crash_reports_diagnostics.py keeps the two byte-for-byte in agreement.
"""
from __future__ import annotations

import re
from typing import Optional

ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"        # 32 symbols, no 0 O 1 I
CODE_LENGTH = 6
_BITS_PER_CHAR = 5
_CODE_BITS = CODE_LENGTH * _BITS_PER_CHAR              # 30
_FINGERPRINT_RE = re.compile(r"^[0-9a-f]{32}$")


def reference_code(fingerprint: str) -> Optional[str]:
    """The 6-character code for a fingerprint, or None if it is not a
    32-character lowercase hex fingerprint (a malformed id must never turn
    into a plausible-looking code)."""
    if not isinstance(fingerprint, str) or not _FINGERPRINT_RE.match(fingerprint):
        return None
    top = int(fingerprint[:8], 16) >> (32 - _CODE_BITS)      # first 30 bits
    chars = []
    for shift in range(_CODE_BITS - _BITS_PER_CHAR, -1, -_BITS_PER_CHAR):
        chars.append(ALPHABET[(top >> shift) & 0x1F])
    return "".join(chars)
