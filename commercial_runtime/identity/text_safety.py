"""One text rule for every human-typed name that travels between computers
(a person's display name, a computer's label).

WHY ONE MODULE. The user-name rule lives in user_accounts.py and the computer
label rule in device_routes.py / device_registry.py, and until the 2026-09-29
security review neither looked at what KIND of character it stored. A name may
carry, invisibly:

  - bidirectional overrides (U+202E RIGHT-TO-LEFT OVERRIDE and friends): a
    label that displays as its own reverse, so "Main till" can be made to read
    as something else in a list an owner uses to decide what to revoke;
  - zero-width and other format characters (U+200B ...): two names that look
    identical but differ, or a name that looks empty;
  - control characters and line breaks: a "name" that breaks a table row, a
    log line, or an audit entry into two.

The shop's names are Arabic-first, so the two joiners that Arabic and Persian
shaping genuinely needs, U+200C ZERO WIDTH NON-JOINER and U+200D ZERO WIDTH
JOINER, are KEPT. Everything else in Unicode categories Cc (control) and Cf
(format) is removed, and a line break is REFUSED rather than silently joined
(a name with a newline in it was never typed by a person filling a one-line
box; it is an attack or a corrupt row).

Stdlib only: this module stays safe to import eagerly on Android.
"""
import unicodedata

# The only Cf characters a real name needs (Arabic/Persian joining behaviour).
_KEPT_FORMAT_CHARS = frozenset('‌‍')

# Anything that ends a line in any common convention. \n \r \x0b \x0c \x1c-\x1e
# and \x85 are Cc; U+2028/U+2029 are Zl/Zp, so they are named explicitly.
_LINE_BREAKS = frozenset('\n\r\x0b\x0c\x1c\x1d\x1e\x85  ')


def has_line_break(text: str) -> bool:
    return any(ch in _LINE_BREAKS for ch in text)


def strip_control_and_format(text: str) -> str:
    """Remove every Cc/Cf character except U+200C / U+200D. Does not trim."""
    return ''.join(
        ch for ch in text
        if ch in _KEPT_FORMAT_CHARS or unicodedata.category(ch) not in ('Cc', 'Cf')
    )


def clean_single_line(raw, *, max_len: int, what: str = 'Name'):
    """NFC, refuse line breaks, strip Cc/Cf, trim; None when nothing is left.

    Raises ValueError on a non-string, a line break, or a result over
    `max_len`. The length is judged AFTER stripping, so invisible padding
    cannot be used to sneak past (or to blow through) the limit."""
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise ValueError(f'{what} must be text.')
    if has_line_break(raw):
        raise ValueError(f'{what} must be a single line.')
    text = unicodedata.normalize('NFC', strip_control_and_format(raw)).strip()
    if not text:
        return None
    if len(text) > max_len:
        raise ValueError(f'{what} must be {max_len} characters or fewer.')
    return text
