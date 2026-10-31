"""Request signing for the website -> Owner account API (v1).

    canonical = "v1\n" + METHOD + "\n" + PATH + "\n" + ts + "\n" + nonce + "\n" + hex(sha256(body))
    sig       = hex(HMAC-SHA256(secret[kid], canonical))
    header    = X-Aura-Site-Signature: v1,kid=<kid>,ts=<unix seconds>,nonce=<16-64 chars>,sig=<hex>

METHOD is upper-case, PATH is the request path without query string (as the
website sent it), ts is whole UNIX seconds (decimal), body is the exact raw bytes.
Two key slots (kid) exist so the secret can be rotated without downtime.
A known-answer vector lives in docs/owner/customer-accounts/signature-v1.md and is
pinned by tests/test_customer_accounts_api.py so the website's TypeScript
implementation can be cross-checked against it.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import time
from dataclasses import dataclass

from app.config import BaseConfig

HEADER = "X-Aura-Site-Signature"
VERSION = "v1"
MAX_SKEW_SECONDS = 60
_NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
_SIG_RE = re.compile(r"^[0-9a-f]{64}$")


class SignatureError(Exception):
    pass


@dataclass(frozen=True)
class ParsedSignature:
    kid: str
    ts: int
    nonce: str
    sig: str


def canonical_string(method: str, path: str, ts: str, nonce: str, body: bytes) -> bytes:
    return "\n".join((VERSION, method.upper(), path, ts, nonce, hashlib.sha256(body).hexdigest())).encode("utf-8")


def sign(secret: str, method: str, path: str, ts: str, nonce: str, body: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), canonical_string(method, path, ts, nonce, body), hashlib.sha256).hexdigest()


def active_keys(config) -> dict:
    """{kid: secret} for every slot holding a REAL secret (placeholder / too short
    = slot not active). Empty dict = the whole API is off."""
    keys = {}
    for kid_key, secret_key in (("WEBSITE_ACCOUNT_KEY_ID", "WEBSITE_ACCOUNT_SECRET"),
                                ("WEBSITE_ACCOUNT_KEY_ID_2", "WEBSITE_ACCOUNT_SECRET_2")):
        secret = (config.get(secret_key) or "").strip()
        kid = (config.get(kid_key) or "").strip()
        if kid and BaseConfig._looks_like_a_real_secret(secret) and kid not in keys:
            keys[kid] = secret
    return keys


def parse_header(value: str) -> ParsedSignature:
    version, _, rest = (value or "").partition(",")
    if version.strip() != VERSION:
        raise SignatureError("bad_version")
    fields = {}
    for part in rest.split(","):
        name, eq, val = part.strip().partition("=")
        if not eq or name in fields:
            raise SignatureError("malformed")
        fields[name] = val
    if set(fields) != {"kid", "ts", "nonce", "sig"}:
        raise SignatureError("malformed")
    if not fields["ts"].isdigit() or len(fields["ts"]) > 12:
        raise SignatureError("malformed")
    if not _NONCE_RE.match(fields["nonce"]) or not _SIG_RE.match(fields["sig"]):
        raise SignatureError("malformed")
    return ParsedSignature(kid=fields["kid"], ts=int(fields["ts"]), nonce=fields["nonce"], sig=fields["sig"])


def verify(config, header_value: str, method: str, path: str, body: bytes, *, now: float | None = None) -> ParsedSignature:
    """Raises SignatureError unless the header is well-formed, the kid is an active
    slot, the timestamp is within 60 s of now, and the HMAC matches (constant time).
    Replay (nonce reuse) is checked by the caller AFTER this passes, so a forged
    request can never burn a real nonce."""
    parsed = parse_header(header_value)
    secret = active_keys(config).get(parsed.kid)
    if secret is None:
        raise SignatureError("unknown_kid")
    if abs((now if now is not None else time.time()) - parsed.ts) > MAX_SKEW_SECONDS:
        raise SignatureError("skew")
    expected = sign(secret, method, path, str(parsed.ts), parsed.nonce, body)
    if not hmac.compare_digest(expected.encode("ascii"), parsed.sig.encode("ascii")):
        raise SignatureError("bad_signature")
    return parsed
