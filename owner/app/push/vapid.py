"""VAPID (RFC 8292): who is sending the push, proven to the push service.

The server holds ONE P-256 key pair. The browser is given the PUBLIC key when
it subscribes (applicationServerKey); every message then carries a short
signed token (ES256 JWT) made with the PRIVATE key, so the push service only
delivers to that subscription what this server signed.

Where the private key lives: OWNER_PUSH_VAPID_PRIVATE_KEY in the server's
environment (.env.oracle on the live VM), as the raw 32-byte key, base64url.
Nowhere else: not in the database, not in a log line, not in an audit row,
not on any page. `flask notifications generate-vapid-keys` makes one and
writes it only into a redirected file (app/push/cli.py).

Kill switch: no key (or a key that does not parse) == push is OFF everywhere
(push_enabled() is the one question every caller asks). A bad key logs ONE
warning naming the variable, never its value.
"""
from __future__ import annotations

import base64
import logging
import time
from dataclasses import dataclass
from functools import lru_cache
from urllib.parse import urlsplit

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from flask import current_app

log = logging.getLogger("aura.owner.push")

#: RFC 8292 caps the token at 24 hours; 12 leaves room for clock skew.
TOKEN_LIFETIME_SECONDS = 12 * 3600


def b64url_decode(value: str) -> bytes:
    value = (value or "").strip()
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


@dataclass(frozen=True)
class VapidKeys:
    private_key: ec.EllipticCurvePrivateKey
    public_key_b64: str  # uncompressed point, base64url: the browser's applicationServerKey
    subject: str

    def __repr__(self) -> str:  # never let a traceback or a log print the key
        return f"VapidKeys(public_key_b64={self.public_key_b64!r}, subject={self.subject!r})"


class VapidKeyError(ValueError):
    """The configured key does not parse. The message never contains it."""


def public_key_b64_of(private_key: ec.EllipticCurvePrivateKey) -> str:
    return b64url_encode(
        private_key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    )


def parse_private_key(raw_b64: str) -> ec.EllipticCurvePrivateKey:
    try:
        raw = b64url_decode(raw_b64)
    except (ValueError, TypeError) as exc:
        raise VapidKeyError("OWNER_PUSH_VAPID_PRIVATE_KEY is not base64url") from exc
    if len(raw) != 32:
        raise VapidKeyError("OWNER_PUSH_VAPID_PRIVATE_KEY must be a 32-byte P-256 private key")
    try:
        return ec.derive_private_key(int.from_bytes(raw, "big"), ec.SECP256R1())
    except ValueError as exc:
        raise VapidKeyError("OWNER_PUSH_VAPID_PRIVATE_KEY is not a valid P-256 private key") from exc


def generate_private_key_b64() -> tuple[str, str]:
    """(private key, public key), both base64url -- for the CLI only."""
    key = ec.generate_private_key(ec.SECP256R1())
    raw = key.private_numbers().private_value.to_bytes(32, "big")
    return b64url_encode(raw), public_key_b64_of(key)


@lru_cache(maxsize=4)
def _keys_for(raw_b64: str, subject: str) -> VapidKeys:
    key = parse_private_key(raw_b64)
    return VapidKeys(private_key=key, public_key_b64=public_key_b64_of(key), subject=subject)


_warned: set[str] = set()


def keys_from_config(config) -> VapidKeys | None:
    """The configured keys, or None when push is off (no key, or a bad one)."""
    raw = (config.get("PUSH_VAPID_PRIVATE_KEY") or "").strip()
    if not raw:
        return None
    subject = (config.get("PUSH_VAPID_SUBJECT") or "").strip()
    if not subject.startswith(("mailto:", "https://")):
        _warn_once("subject", "push notifications are OFF: OWNER_PUSH_VAPID_SUBJECT must start with mailto: or https://")
        return None
    try:
        return _keys_for(raw, subject)
    except VapidKeyError as exc:
        _warn_once("key", f"push notifications are OFF: {exc}")
        return None


def _warn_once(kind: str, message: str) -> None:
    if kind not in _warned:
        _warned.add(kind)
        log.warning(message)


def push_enabled(config=None) -> bool:
    return keys_from_config(config if config is not None else current_app.config) is not None


def authorization_header(endpoint: str, keys: VapidKeys, *, now: float | None = None) -> str:
    """`Authorization: vapid t=<jwt>, k=<public key>` for one message to `endpoint`.
    The audience is the push service's origin (scheme://host), never the full
    endpoint: the token must not carry the capability URL."""
    parts = urlsplit(endpoint)
    audience = f"{parts.scheme}://{parts.netloc}"
    issued = int(now if now is not None else time.time())
    token = jwt.encode(
        {"aud": audience, "exp": issued + TOKEN_LIFETIME_SECONDS, "sub": keys.subject},
        keys.private_key,
        algorithm="ES256",
    )
    return f"vapid t={token}, k={keys.public_key_b64}"


def warn_if_key_unusable(app) -> None:
    """One startup line when a key is set but push is still off -- so an
    operator who set it and sees no button learns why from the log."""
    if (app.config.get("PUSH_VAPID_PRIVATE_KEY") or "").strip():
        keys_from_config(app.config)
