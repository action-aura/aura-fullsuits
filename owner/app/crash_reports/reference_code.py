"""Crash reports -- the 6-character reference code, Owner's copy.

A shopkeeper reads "K7PX2M" off their screen (or from a WhatsApp message) and
staff type it into the crash-reports page. The code is the first 30 bits of the
report's 32-hex fingerprint written in a 32-symbol alphabet with no 0, O, 1 or I.

This is a COPY of commercial_runtime/crash_reports/reference_code.py: Owner is
deployed on its own and cannot import the products' package. The two must
agree character for character, and tests/test_crash_reports_reference_code.py
fails if they ever do not (it imports both and compares them on fixed and
random fingerprints).

Owner also needs the INVERSE, which the till never does: from a typed code,
which fingerprints could it mean? 30 bits fix the first 7.5 hex digits, so the
answer is a contiguous range of fingerprints, searchable with a plain BETWEEN
on the indexed column instead of computing a code for every row.
"""
from __future__ import annotations

import re
from typing import Optional, Tuple

ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"        # 32 symbols, no 0 O 1 I
CODE_LENGTH = 6
_BITS_PER_CHAR = 5
_CODE_BITS = CODE_LENGTH * _BITS_PER_CHAR              # 30
_FINGERPRINT_RE = re.compile(r"^[0-9a-f]{32}$")


def reference_code(fingerprint: str) -> Optional[str]:
    if not isinstance(fingerprint, str) or not _FINGERPRINT_RE.match(fingerprint):
        return None
    top = int(fingerprint[:8], 16) >> (32 - _CODE_BITS)
    return "".join(ALPHABET[(top >> shift) & 0x1F] for shift in range(_CODE_BITS - _BITS_PER_CHAR, -1, -_BITS_PER_CHAR))


def fingerprint_range(code: str) -> Optional[Tuple[str, str]]:
    """(lowest, highest) 32-hex fingerprint that `code` can stand for, or None
    if what was typed is not a code. Case and surrounding spaces are forgiven
    (people type it from a phone call); a wrong LENGTH or a character outside
    the alphabet (0, O, 1, I, anything else) is not a code at all and must
    match nothing rather than something near it."""
    if not isinstance(code, str):
        return None
    text = code.strip().upper()
    if len(text) != CODE_LENGTH or any(ch not in ALPHABET for ch in text):
        return None
    value = 0
    for ch in text:
        value = (value << _BITS_PER_CHAR) | ALPHABET.index(ch)
    low = value << (32 - _CODE_BITS)                    # the top 30 of 32 bits; 2 free bits
    high = low | ((1 << (32 - _CODE_BITS)) - 1)
    return ("%08x" % low) + "0" * 24, ("%08x" % high) + "f" * 24
