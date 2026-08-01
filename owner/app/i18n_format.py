"""Phase 9.5B-R Milestone 13 -- centralized locale-aware date/number
presentation helpers.

Arabic-digit policy (explicit, documented decision -- governing spec's own
requirement): Western (ASCII 0-9) digits are used consistently in both
locales, never Eastern Arabic-Indic digits. Reason: Aura Owner is a
business/back-office system where employee numbers, dates, session counts,
and every other numeral must remain visually and semantically consistent
with the identifiers they sit next to. Verified real behavior (not assumed):
Babel 2.18's own default `numbering_system` for `format_decimal`/
`format_date`/`format_datetime` is already `"latn"` (Western digits) for
locale `ar`, confirmed by direct inspection (`babel.numbers.format_decimal`'s
signature default and a real formatted `ar`-locale date, whose digit
characters are U+0030-U+0039, not U+0660-U+0669) -- so this policy holds
using Babel's own default, with nothing to override. Recorded here as a
verified decision, not a guess, and re-checked by
test_phase9_5b_r_formatting.py so a future Babel upgrade changing this
default would be caught, not silently shipped.

None of this ever touches a stored value -- these helpers only ever format
a value for display; the underlying Decimal/datetime/int is never mutated
(Non-Negotiable Principle 3/13's own "do not change financial calculations"
requirement).
"""
from __future__ import annotations

from datetime import date, datetime

from flask_babel import format_date as _babel_format_date
from flask_babel import format_datetime as _babel_format_datetime
from flask_babel import format_decimal as _babel_format_decimal


def format_owner_date(value: date | None) -> str:
    if value is None:
        return "-"
    return _babel_format_date(value, format="medium")


def format_owner_datetime(value: datetime | None) -> str:
    if value is None:
        return "-"
    return _babel_format_datetime(value, format="medium")


def format_owner_number(value) -> str:
    """Never used on an identifier (employee number, UUID, phone, version,
    correlation ID) -- only a real count/quantity. Callers are responsible
    for choosing the right helper; this one intentionally has no identifier-
    shaped guard, since 'looks numeric' is not the same test as 'is a
    quantity' (an employee number can be all digits)."""
    if value is None:
        return "-"
    return _babel_format_decimal(value)
