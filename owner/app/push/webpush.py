"""The Web Push transport: encrypt one message to one device, POST it.

WHY NOT pywebpush. The standard Python library (pywebpush 2.5.0, checked
2026-10-25) requires aiohttp -- eight more compiled packages for an async
client this server never uses -- plus http-ece, py-vapid and requests. What
it does for us is two small, fully specified steps, and both primitives are
already pinned in requirements/owner-server.txt:
  * the payload encryption, RFC 8291 (aes128gcm, RFC 8188): ECDH P-256 +
    HKDF-SHA256 + AES-128-GCM -- `cryptography`;
  * the VAPID token, RFC 8292: an ES256 JWT -- `pyjwt` (app/push/vapid.py).
tests/test_step1_push_transport.py reproduces RFC 8291 section 5's worked
example byte for byte (same keys, same salt -> the RFC's exact message), and
the real-browser proof (a real Edge subscribed through its real push service
and its service worker decrypted and showed the message) is in the commit.

WHERE IT MAY POST. A subscription's endpoint comes from the browser, through
a staff member's request, so it is outside input: this server would POST to
whatever URL was stored. Only https on port 443 at the browser push services
we know is accepted (ALLOWED_PUSH_HOST_SUFFIXES) -- a stored endpoint can
never point this server at an internal address. A browser on a push service
not in the list gets a clear refusal at subscribe time, not a silent failure.

Nothing here logs. Errors are reported as short codes, never as response
bodies or exception text, which can echo the endpoint (a capability URL).
"""
from __future__ import annotations

import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.push.vapid import VapidKeys, authorization_header, b64url_decode

#: Chrome/Android (FCM), Firefox (Mozilla autopush), Edge (Windows Push
#: Notification Services), Safari / iPhone home-screen apps (Apple).
ALLOWED_PUSH_HOST_SUFFIXES = (
    "fcm.googleapis.com",
    "android.googleapis.com",
    ".push.services.mozilla.com",
    ".notify.windows.com",
    ".push.apple.com",
)
MAX_ENDPOINT_LENGTH = 2048
#: RFC 8188 record size. One record carries the whole message: the payload
#: is a few hundred bytes, far under the 4096-byte push-service limit.
RECORD_SIZE = 4096
MAX_PLAINTEXT_BYTES = RECORD_SIZE - 16 - 1 - 86  # tag, delimiter, header -- keeps it to one record
REQUEST_TIMEOUT_SECONDS = 10


class InvalidSubscription(ValueError):
    """A subscription this server will not store or send to. str() is a code."""


class PushTransportError(Exception):
    """The push service could not be reached (DNS, TLS, timeout). str() is a code."""


@dataclass(frozen=True)
class PushResponse:
    status: int
    retry_after_seconds: float | None = None


def _host_allowed(host: str) -> bool:
    host = (host or "").lower().rstrip(".")
    for suffix in ALLOWED_PUSH_HOST_SUFFIXES:
        if suffix.startswith("."):
            if host.endswith(suffix) and len(host) > len(suffix):
                return True
        elif host == suffix:
            return True
    return False


def validate_endpoint(endpoint: str) -> str:
    if not isinstance(endpoint, str) or not endpoint or len(endpoint) > MAX_ENDPOINT_LENGTH:
        raise InvalidSubscription("ENDPOINT_MISSING_OR_TOO_LONG")
    if any(ch.isspace() for ch in endpoint):
        raise InvalidSubscription("ENDPOINT_INVALID")
    parts = urlsplit(endpoint)
    if parts.scheme != "https" or parts.username or parts.password:
        raise InvalidSubscription("ENDPOINT_NOT_HTTPS")
    try:
        port = parts.port
    except ValueError as exc:
        raise InvalidSubscription("ENDPOINT_INVALID") from exc
    if port not in (None, 443):
        raise InvalidSubscription("ENDPOINT_PORT_NOT_ALLOWED")
    if not _host_allowed(parts.hostname or ""):
        raise InvalidSubscription("ENDPOINT_HOST_NOT_ALLOWED")
    return endpoint


def validate_keys(p256dh: str, auth: str) -> tuple[str, str]:
    """The browser's keys: a valid P-256 public point and a 16-byte secret."""
    try:
        point = b64url_decode(p256dh)
        secret = b64url_decode(auth)
    except (ValueError, TypeError) as exc:
        raise InvalidSubscription("KEYS_NOT_BASE64URL") from exc
    if len(point) != 65 or point[0] != 0x04:
        raise InvalidSubscription("P256DH_INVALID")
    try:
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), point)
    except ValueError as exc:
        raise InvalidSubscription("P256DH_INVALID") from exc
    if len(secret) != 16:
        raise InvalidSubscription("AUTH_INVALID")
    return p256dh.strip(), auth.strip()


def encrypt(plaintext: bytes, *, p256dh: str, auth: str, salt: bytes | None = None,
            sender_private_key: ec.EllipticCurvePrivateKey | None = None) -> bytes:
    """RFC 8291 message encryption (aes128gcm). `salt` and
    `sender_private_key` are fresh random values per message; they are
    parameters only so the RFC's worked example can be reproduced exactly."""
    if len(plaintext) > MAX_PLAINTEXT_BYTES:
        raise ValueError("push payload too large")
    ua_public_bytes = b64url_decode(p256dh)
    auth_secret = b64url_decode(auth)
    ua_public = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public_bytes)
    sender = sender_private_key or ec.generate_private_key(ec.SECP256R1())
    sender_public_bytes = sender.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    salt = salt if salt is not None else os.urandom(16)

    ecdh_secret = sender.exchange(ec.ECDH(), ua_public)
    key_info = b"WebPush: info\x00" + ua_public_bytes + sender_public_bytes
    ikm = HKDF(algorithm=hashes.SHA256(), length=32, salt=auth_secret, info=key_info).derive(ecdh_secret)
    cek = HKDF(algorithm=hashes.SHA256(), length=16, salt=salt, info=b"Content-Encoding: aes128gcm\x00").derive(ikm)
    nonce = HKDF(algorithm=hashes.SHA256(), length=12, salt=salt, info=b"Content-Encoding: nonce\x00").derive(ikm)
    # 0x02: the padding delimiter of the LAST (here: only) record.
    ciphertext = AESGCM(cek).encrypt(nonce, plaintext + b"\x02", None)
    header = salt + RECORD_SIZE.to_bytes(4, "big") + bytes([len(sender_public_bytes)]) + sender_public_bytes
    return header + ciphertext


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A push service answers 201/4xx/5xx; it never redirects. Following one
    would let the allow-listed host send this server somewhere else."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def _retry_after(headers) -> float | None:
    value = headers.get("Retry-After") if headers is not None else None
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None  # an HTTP-date; the normal backoff applies


def send(*, endpoint: str, p256dh: str, auth: str, payload: bytes, keys: VapidKeys, ttl_seconds: int,
         topic: str | None = None, urgency: str = "high") -> PushResponse:
    """POST one encrypted message. Returns the push service's status (201 =
    accepted). Raises PushTransportError when no answer came at all."""
    validate_endpoint(endpoint)  # re-checked at send time, not only at subscribe time
    body = encrypt(payload, p256dh=p256dh, auth=auth)
    headers = {
        "Authorization": authorization_header(endpoint, keys),
        "Content-Encoding": "aes128gcm",
        "Content-Type": "application/octet-stream",
        "TTL": str(int(ttl_seconds)),
        # "high": delivered at once even to a phone in battery saving -- a
        # sale is waiting for a person (RFC 8030 section 5.3).
        "Urgency": urgency,
    }
    if topic:
        # A newer message with the same topic REPLACES one the push service
        # is still holding for an offline phone (RFC 8030 section 5.4).
        headers["Topic"] = topic
    request = urllib.request.Request(endpoint, data=body, headers=headers, method="POST")
    try:
        with _OPENER.open(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:  # noqa: S310 -- https, allow-listed host
            return PushResponse(status=response.status, retry_after_seconds=_retry_after(response.headers))
    except urllib.error.HTTPError as exc:
        return PushResponse(status=exc.code, retry_after_seconds=_retry_after(exc.headers))
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise PushTransportError(type(exc).__name__) from None
