"""Step 1 (2026-10-25) -- the Web Push transport, without a database.

app/push/webpush.py encrypts and sends with `cryptography` + `pyjwt` (both
already pinned) instead of pywebpush (which pulls aiohttp). That is only
acceptable if it is byte-exact to the standard, so:
  * RFC 8291 section 5's worked example is reproduced byte for byte;
  * the VAPID token verifies against the public key and names only the push
    service's origin;
  * the request that would go on the wire is captured and DECRYPTED with the
    browser's private key;
  * the endpoint allow-list refuses every look-alike host, other ports and http.
The real-browser proof (real Edge, real push service, real service worker)
is recorded in the commit; it cannot run in CI.
"""
from __future__ import annotations

import json
import urllib.error

import jwt
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.push import vapid, webpush
from app.push.vapid import b64url_decode, b64url_encode

# RFC 8291 section 5 (Push Message Encryption Example).
RFC_PLAINTEXT = b"When I grow up, I want to be a watermelon"
RFC_AS_PRIVATE = "yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"
RFC_UA_PRIVATE = "q1dXpw3UpT5VOmu_cf_v6ih07Aems3njxI-JWgLcM94"
RFC_UA_PUBLIC = "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4"
RFC_AUTH = "BTBZMqHH6r4Tts7J_aSIgg"
RFC_SALT = "DGv6ra1nlYgDCS1FRnbzlw"
RFC_MESSAGE = (
    "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBr"
    "u3jl7A_yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN"
)


def _key(b64: str) -> ec.EllipticCurvePrivateKey:
    return ec.derive_private_key(int.from_bytes(b64url_decode(b64), "big"), ec.SECP256R1())


def _decrypt(body: bytes, *, ua_private_b64: str, auth_b64: str) -> bytes:
    """The BROWSER side of RFC 8291, written independently of webpush.encrypt."""
    salt, record_size, idlen = body[:16], int.from_bytes(body[16:20], "big"), body[20]
    sender_public = body[21:21 + idlen]
    ciphertext = body[21 + idlen:]
    assert record_size == 4096 and idlen == 65
    ua_private = _key(ua_private_b64)
    ua_public = ua_private.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    secret = ua_private.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), sender_public))
    ikm = HKDF(hashes.SHA256(), 32, b64url_decode(auth_b64), b"WebPush: info\x00" + ua_public + sender_public).derive(secret)
    cek = HKDF(hashes.SHA256(), 16, salt, b"Content-Encoding: aes128gcm\x00").derive(ikm)
    nonce = HKDF(hashes.SHA256(), 12, salt, b"Content-Encoding: nonce\x00").derive(ikm)
    plain = AESGCM(cek).decrypt(nonce, ciphertext, None)
    assert plain.endswith(b"\x02")
    return plain[:-1]


def test_encryption_reproduces_rfc8291_worked_example_byte_for_byte():
    body = webpush.encrypt(
        RFC_PLAINTEXT, p256dh=RFC_UA_PUBLIC, auth=RFC_AUTH,
        salt=b64url_decode(RFC_SALT), sender_private_key=_key(RFC_AS_PRIVATE),
    )
    assert b64url_encode(body) == RFC_MESSAGE


def test_fresh_encryption_decrypts_with_the_browsers_key_and_never_repeats():
    a = webpush.encrypt(b'{"title":"x"}', p256dh=RFC_UA_PUBLIC, auth=RFC_AUTH)
    b = webpush.encrypt(b'{"title":"x"}', p256dh=RFC_UA_PUBLIC, auth=RFC_AUTH)
    assert a != b  # fresh salt and sender key per message
    assert _decrypt(a, ua_private_b64=RFC_UA_PRIVATE, auth_b64=RFC_AUTH) == b'{"title":"x"}'
    assert _decrypt(b, ua_private_b64=RFC_UA_PRIVATE, auth_b64=RFC_AUTH) == b'{"title":"x"}'


def _keys(subject="mailto:info@actionaura.me"):
    private_b64, _public = vapid.generate_private_key_b64()
    return vapid.keys_from_config({"PUSH_VAPID_PRIVATE_KEY": private_b64, "PUSH_VAPID_SUBJECT": subject})


def test_vapid_header_verifies_and_names_only_the_push_service_origin():
    keys = _keys()
    header = vapid.authorization_header("https://fcm.googleapis.com/fcm/send/abc:def", keys, now=1_800_000_000)
    assert header.startswith("vapid t=") and header.endswith(f", k={keys.public_key_b64}")
    token = header[len("vapid t="):header.index(", k=")]
    public = keys.private_key.public_key()
    claims = jwt.decode(token, public, algorithms=["ES256"], audience="https://fcm.googleapis.com",
                        options={"verify_exp": False})
    assert claims == {"aud": "https://fcm.googleapis.com", "exp": 1_800_000_000 + 12 * 3600, "sub": "mailto:info@actionaura.me"}
    assert "abc" not in token  # the capability URL never travels in the token
    assert len(b64url_decode(keys.public_key_b64)) == 65


def test_keys_from_config_is_the_kill_switch_and_never_echoes_a_bad_key(caplog):
    assert vapid.keys_from_config({"PUSH_VAPID_PRIVATE_KEY": "", "PUSH_VAPID_SUBJECT": "mailto:a@b.c"}) is None
    secret_looking = "not-a-key-but-secret-looking-value-123456"
    with caplog.at_level("WARNING", logger="aura.owner.push"):
        assert vapid.keys_from_config({"PUSH_VAPID_PRIVATE_KEY": secret_looking, "PUSH_VAPID_SUBJECT": "mailto:a@b.c"}) is None
    assert secret_looking not in caplog.text
    good, _public = vapid.generate_private_key_b64()
    assert vapid.keys_from_config({"PUSH_VAPID_PRIVATE_KEY": good, "PUSH_VAPID_SUBJECT": "http://x"}) is None
    keys = vapid.keys_from_config({"PUSH_VAPID_PRIVATE_KEY": good, "PUSH_VAPID_SUBJECT": "https://actionaura.me"})
    assert keys is not None and good not in repr(keys)


@pytest.mark.parametrize("endpoint", [
    "https://fcm.googleapis.com/fcm/send/abc",
    "https://wns2-par02p.notify.windows.com/w/?token=abc",
    "https://web.push.apple.com/QGuQyavXutnMH",
    "https://updates.push.services.mozilla.com/wpush/v2/abc",
    "https://fcm.googleapis.com:443/fcm/send/abc",
])
def test_known_push_services_are_accepted(endpoint):
    assert webpush.validate_endpoint(endpoint) == endpoint


@pytest.mark.parametrize("endpoint,code", [
    ("http://fcm.googleapis.com/fcm/send/abc", "ENDPOINT_NOT_HTTPS"),
    ("https://fcm.googleapis.com.attacker.example/x", "ENDPOINT_HOST_NOT_ALLOWED"),
    ("https://evilfcm.googleapis.com/x", "ENDPOINT_HOST_NOT_ALLOWED"),
    ("https://notify.windows.com.attacker.example/x", "ENDPOINT_HOST_NOT_ALLOWED"),
    ("https://attacker-notify.windows.com/x", "ENDPOINT_HOST_NOT_ALLOWED"),
    ("https://.notify.windows.com/x", "ENDPOINT_HOST_NOT_ALLOWED"),
    ("https://127.0.0.1/x", "ENDPOINT_HOST_NOT_ALLOWED"),
    ("https://localhost/x", "ENDPOINT_HOST_NOT_ALLOWED"),
    ("https://fcm.googleapis.com:8443/x", "ENDPOINT_PORT_NOT_ALLOWED"),
    ("https://user:pw@fcm.googleapis.com/x", "ENDPOINT_NOT_HTTPS"),
    ("https://fcm.googleapis.com/" + "a" * 3000, "ENDPOINT_MISSING_OR_TOO_LONG"),
    ("", "ENDPOINT_MISSING_OR_TOO_LONG"),
    ("https://fcm.googleapis.com/a b", "ENDPOINT_INVALID"),
])
def test_everything_else_is_refused(endpoint, code):
    with pytest.raises(webpush.InvalidSubscription) as exc:
        webpush.validate_endpoint(endpoint)
    assert str(exc.value) == code


def test_browser_keys_are_validated():
    assert webpush.validate_keys(RFC_UA_PUBLIC, RFC_AUTH) == (RFC_UA_PUBLIC, RFC_AUTH)
    for p256dh, auth, code in [
        ("AAAA", RFC_AUTH, "P256DH_INVALID"),
        ("B" + RFC_UA_PUBLIC[1:-4] + "AAAA", RFC_AUTH, "P256DH_INVALID"),  # 65 bytes, not on the curve
        (RFC_UA_PUBLIC, "AAAA", "AUTH_INVALID"),
    ]:
        with pytest.raises(webpush.InvalidSubscription) as exc:
            webpush.validate_keys(p256dh, auth)
        assert str(exc.value) == code


class _FakeResponse:
    def __init__(self, status, headers=None):
        self.status = status
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_send_puts_a_decryptable_standard_request_on_the_wire(monkeypatch):
    captured = {}

    def fake_open(request, timeout):
        captured["request"], captured["timeout"] = request, timeout
        return _FakeResponse(201)

    monkeypatch.setattr(webpush._OPENER, "open", fake_open)
    keys = _keys()
    payload = json.dumps({"title": "New sale waiting for confirmation"}).encode()
    result = webpush.send(endpoint="https://fcm.googleapis.com/fcm/send/xyz", p256dh=RFC_UA_PUBLIC, auth=RFC_AUTH,
                          payload=payload, keys=keys, ttl_seconds=86400, topic="sales-waiting")
    assert result == webpush.PushResponse(status=201, retry_after_seconds=None)
    request = captured["request"]
    headers = {k.lower(): v for k, v in request.header_items()}
    assert request.get_method() == "POST" and request.full_url == "https://fcm.googleapis.com/fcm/send/xyz"
    assert headers["content-encoding"] == "aes128gcm"
    assert headers["content-type"] == "application/octet-stream"
    assert headers["ttl"] == "86400" and headers["urgency"] == "high" and headers["topic"] == "sales-waiting"
    assert headers["authorization"].startswith("vapid t=") and keys.public_key_b64 in headers["authorization"]
    assert captured["timeout"] == webpush.REQUEST_TIMEOUT_SECONDS
    assert _decrypt(request.data, ua_private_b64=RFC_UA_PRIVATE, auth_b64=RFC_AUTH) == payload


def test_send_maps_http_errors_and_network_failures(monkeypatch):
    keys = _keys()

    def gone(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 410, "Gone", {"Retry-After": "120"}, None)

    monkeypatch.setattr(webpush._OPENER, "open", gone)
    assert webpush.send(endpoint="https://fcm.googleapis.com/x", p256dh=RFC_UA_PUBLIC, auth=RFC_AUTH, payload=b"{}",
                        keys=keys, ttl_seconds=60) == webpush.PushResponse(status=410, retry_after_seconds=120.0)

    def down(request, timeout):
        raise urllib.error.URLError("no route to fcm.googleapis.com/x secret endpoint")

    monkeypatch.setattr(webpush._OPENER, "open", down)
    with pytest.raises(webpush.PushTransportError) as exc:
        webpush.send(endpoint="https://fcm.googleapis.com/x", p256dh=RFC_UA_PUBLIC, auth=RFC_AUTH, payload=b"{}",
                     keys=keys, ttl_seconds=60)
    assert str(exc.value) == "URLError"  # a code, never the message (it can echo the endpoint)


def test_send_rechecks_the_endpoint_and_never_follows_a_redirect(monkeypatch):
    keys = _keys()
    monkeypatch.setattr(webpush._OPENER, "open", lambda request, timeout: pytest.fail("must not POST"))
    with pytest.raises(webpush.InvalidSubscription):
        webpush.send(endpoint="https://10.0.0.5/internal", p256dh=RFC_UA_PUBLIC, auth=RFC_AUTH, payload=b"{}",
                     keys=keys, ttl_seconds=60)
    handler = webpush._NoRedirect()
    assert handler.redirect_request(None, None, 307, "Temporary Redirect", {}, "https://10.0.0.5/") is None


def test_generated_keys_round_trip():
    private_b64, public_b64 = vapid.generate_private_key_b64()
    assert len(b64url_decode(private_b64)) == 32
    assert vapid.public_key_b64_of(vapid.parse_private_key(private_b64)) == public_b64
