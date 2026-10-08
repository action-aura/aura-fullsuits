"""Semantic-version precedence (semver.org 2.0.0 section 11) for release discovery.

Why this exists: the update-discovery endpoint must answer "what is the newest
published release for this install?" Comparing the stored `version` strings as
text gets the very first real case wrong -- "1.0.0-rc.9" > "1.0.0-rc.10" as
text, and "1.0.0-rc.99" > "1.0.0" as text -- so a client on rc.9 would never be
offered rc.10, and a client on a 1.0.0-rc.x would be told the final 1.0.0 is
older than its own release candidate. A real comparator is the only fix.

KEEP IDENTICAL to commercial_runtime/updates/versioning.py. Owner is a separate
stack (its own venv, its own deploy) and cannot import from commercial_runtime,
so the same rules live in both places and are pinned by the same test vectors
(owner/tests/test_release_discovery.py and the commercial_runtime copy). If one
side is changed, change the other in the same commit.

Pure functions, no database, no Flask.
"""
from __future__ import annotations

import re
from typing import Tuple

# Exactly the grammar agreed in the update-channel wire contract. `\Z` (not `$`)
# so a trailing newline can never sneak through; a version string ends up in
# file names and URLs on the client, so "almost valid" must mean "rejected".
_SEMVER_RE = re.compile(
    r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+[0-9A-Za-z.-]+)?\Z",
    re.ASCII,  # \d must mean 0-9 only, not Arabic-Indic or other Unicode digits
)

SemverKey = Tuple[int, int, int, tuple]


def _prerelease_key(prerelease: str | None) -> tuple:
    # A release WITH a pre-release tag is LOWER than the same core without one,
    # so "no tag" sorts after "has a tag": (1,) > (0, ...).
    if prerelease is None:
        return (1,)
    ids = []
    for ident in prerelease.split("."):
        if ident.isdigit():
            ids.append((0, int(ident), ""))  # numeric ids compare as numbers, and sort below alphanumeric ones
        else:
            ids.append((1, 0, ident))  # alphanumeric ids compare as ASCII text
    # Tuple comparison also gives rule "a longer set wins when all shared ids are equal".
    return (0, tuple(ids))


def parse_semver(value: str) -> SemverKey:
    """Return a key such that `parse_semver(a) < parse_semver(b)` iff a has lower
    precedence than b. Build metadata (`+abc`) is ignored, per the spec.
    Raises ValueError for anything that is not a strict semver string."""
    if not isinstance(value, str):
        raise ValueError("version must be a string")
    match = _SEMVER_RE.match(value)
    if match is None:
        raise ValueError(f"not a valid semantic version: {value!r}")
    major, minor, patch, prerelease = match.groups()
    return (int(major), int(minor), int(patch), _prerelease_key(prerelease))


def is_valid_semver(value: str) -> bool:
    try:
        parse_semver(value)
    except ValueError:
        return False
    return True


def compare_semver(a: str, b: str) -> int:
    """-1 if a < b, 0 if equal precedence, 1 if a > b."""
    ka, kb = parse_semver(a), parse_semver(b)
    return (ka > kb) - (ka < kb)
