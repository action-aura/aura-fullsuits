"""Semantic Versioning 2.0 precedence, pure and dependency-free.

KEPT IDENTICAL to owner/app/releases/versioning.py (Owner is a separate stack,
so it cannot be imported from here). Both sides are tested against the same
vectors; if you change one, change the other. The two must agree because Owner
decides what to OFFER with this comparator and the product decides whether to
ACCEPT it with this one -- a disagreement would be an offer the client
silently refuses, or worse a downgrade it accepts.

Why not compare strings or tuples of ints: "1.0.0-rc.10" must beat
"1.0.0-rc.9" (lexicographic says the opposite), and "1.0.0" must beat
"1.0.0-rc.99" (a naive int split cannot even parse the latter).
"""
from __future__ import annotations

import re

# re.ASCII: without it \d also matches Arabic-Indic and other Unicode digits,
# which int() happily parses -- a version string must be plain ASCII.
_SEMVER_RE = re.compile(
    r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+[0-9A-Za-z.-]+)?",
    re.ASCII,
)


def parse_semver(version) -> tuple:
    """Return a sortable key for `version`, or raise ValueError.

    fullmatch, not match/`$`: `$` accepts a trailing newline, and this value is
    later used to build a file name.
    """
    if not isinstance(version, str):
        raise ValueError("version must be a string")
    m = _SEMVER_RE.fullmatch(version)
    if m is None:
        raise ValueError("not a semantic version: %r" % (version[:40],))
    major, minor, patch = int(m.group(1)), int(m.group(2)), int(m.group(3))
    pre = m.group(4)
    if pre is None:
        # A release outranks every pre-release of the same core (semver s11.3).
        return (major, minor, patch, 1, ())
    idents = []
    for ident in pre.split("."):
        # Numeric identifiers compare as numbers and rank BELOW alphanumeric
        # ones (s11.4.1, s11.4.3). A longer identifier list wins when all
        # earlier ones are equal -- tuple comparison gives that for free.
        idents.append((0, int(ident), "") if ident.isdigit() else (1, 0, ident))
    return (major, minor, patch, 0, tuple(idents))


def is_valid_semver(version) -> bool:
    try:
        parse_semver(version)
    except ValueError:
        return False
    return True


def compare_versions(a: str, b: str) -> int:
    ka, kb = parse_semver(a), parse_semver(b)
    return (ka > kb) - (ka < kb)


def is_newer(candidate: str, current: str) -> bool:
    """True only when `candidate` is STRICTLY newer than `current`."""
    return compare_versions(candidate, current) > 0
