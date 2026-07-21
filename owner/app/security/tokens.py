"""Cryptographically random bearer tokens (sessions, invitations).

Only a SHA-256 hash of the token is ever stored server-side -- the raw token
exists only in the cookie / the one-time invitation link, never in the database.
"""
from __future__ import annotations

import hashlib
import secrets


def generate_token(nbytes: int = 32) -> str:
    """>= 256 bits of entropy by default."""
    return secrets.token_urlsafe(nbytes)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
