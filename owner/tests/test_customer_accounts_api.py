"""The website -> Owner account API (/api/website/v1/account/*).

Every guard is proven by the row it did or did not write: default-off 404 (both a
missing and a placeholder secret), the signature (bad / wrong path / tampered body /
unknown kid / skew), nonce replay, and the two-key rotation -- each with the allow
direction (a correctly signed request works) next to it."""
from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid

import pytest
from sqlalchemy import func, select

from tests.test_customer_accounts_service import GOOD, IP, active_account, setup_accounts

SECRET = "w" * 48
SECRET2 = "n" * 48
BASE = "/api/website/v1/account"

# The documented known-answer vector (docs/owner/customer-accounts/signature-v1.md).
KAT_SECRET = "aura-kat-secret-0123456789abcdefghijklmnopqrstuv"
KAT_BODY = b'{"email":"owner@haddad.example","password":"Tr0ub4dor-and-3-horses","client_ip":"203.0.113.5","lang":"en"}'
KAT_SHA = "9c16876004ff573925af9b456c4f2c01da3eb1c3a9cc51bb0eb61109bba27448"
KAT_SIG = "729cdd1adc289d537d1bd21a5fd2844adcb1ab6e5fde59f8b669623a5e19bb0a"


def enable(app, **extra):
    setup_accounts(app)
    app.config.update(WEBSITE_ACCOUNT_SECRET=SECRET, WEBSITE_ACCOUNT_KEY_ID="k1", WEBSITE_ACCOUNT_SECRET_2="",
                      WEBSITE_ACCOUNT_KEY_ID_2="k2", **extra)


def header(path, body: bytes, *, secret=SECRET, kid="k1", ts=None, nonce=None, method="POST", sig=None):
    ts = str(int(time.time()) if ts is None else ts)
    nonce = nonce or uuid.uuid4().hex
    canonical = "\n".join(("v1", method, path, ts, nonce, hashlib.sha256(body).hexdigest())).encode()
    sig = sig or hmac.new(secret.encode(), canonical, hashlib.sha256).hexdigest()
    return f"v1,kid={kid},ts={ts},nonce={nonce},sig={sig}"


def call(client, action, payload, **kw):
    path = f"{BASE}/{action}"
    body = json.dumps(payload).encode()
    headers = kw.pop("headers", None)
    if headers is None:
        headers = {"X-Aura-Site-Signature": header(path, body, **kw)}
    return client.post(path, data=body, headers=headers, content_type="application/json")


def nonce_count(app):
    with app.app_context():
        from app.extensions import db_session
        from app.models.licensing_service import SecurityNonceRecord

        return db_session.execute(select(func.count()).select_from(SecurityNonceRecord)
                                  .where(SecurityNonceRecord.scope == "website_account")).scalar_one()


def make_active(app, email="owner@haddad.example"):
    with app.app_context():
        return active_account(app, email)


# ------------------------------------------------------------- the vector

def test_known_answer_vector():
    """The same vector the website's TypeScript is checked against. Computed
    independently with openssl; if this fails the Python drifted from the doc."""
    from app.customer_accounts import signature

    assert hashlib.sha256(KAT_BODY).hexdigest() == KAT_SHA
    assert signature.sign(KAT_SECRET, "POST", "/api/website/v1/account/sign-in", "1790000000",
                          "kat-nonce-0123456789ab", KAT_BODY) == KAT_SIG
    header_value = f"v1,kid=k1,ts=1790000000,nonce=kat-nonce-0123456789ab,sig={KAT_SIG}"
    parsed = signature.verify({"WEBSITE_ACCOUNT_KEY_ID": "k1", "WEBSITE_ACCOUNT_SECRET": KAT_SECRET}, header_value,
                              "POST", "/api/website/v1/account/sign-in", KAT_BODY, now=1790000030)
    assert (parsed.kid, parsed.ts, parsed.nonce) == ("k1", 1790000000, "kat-nonce-0123456789ab")
    # deny: one byte of body, path or method changes the answer
    for method, path, body in (("POST", "/api/website/v1/account/sign-in", KAT_BODY + b" "),
                               ("POST", "/api/website/v1/account/me", KAT_BODY),
                               ("GET", "/api/website/v1/account/sign-in", KAT_BODY)):
        with pytest.raises(signature.SignatureError):
            signature.verify({"WEBSITE_ACCOUNT_KEY_ID": "k1", "WEBSITE_ACCOUNT_SECRET": KAT_SECRET}, header_value,
                             method, path, body, now=1790000030)


# ------------------------------------------------------------- default off

def test_without_a_secret_every_path_answers_404_and_writes_nothing(app, client):
    setup_accounts(app)
    app.config.update(WEBSITE_ACCOUNT_SECRET="", WEBSITE_ACCOUNT_SECRET_2="")
    for action in ("sign-in", "sign-out", "me", "password/forgot", "password/set", "password/change", "email/confirm"):
        path = f"{BASE}/{action}"
        body = b"{}"
        assert client.post(path, data=body, headers={"X-Aura-Site-Signature": header(path, body)}).status_code == 404
        assert client.get(path).status_code == 404
    assert nonce_count(app) == 0


@pytest.mark.parametrize("secret", ["short", "changeme-changeme-changeme-changeme-changeme", "test-secret-" + "x" * 40])
def test_a_placeholder_or_short_secret_also_means_404(app, client, secret):
    setup_accounts(app)
    app.config.update(WEBSITE_ACCOUNT_SECRET=secret, WEBSITE_ACCOUNT_SECRET_2="")
    assert call(client, "sign-in", {}, secret=secret).status_code == 404


def test_with_a_real_secret_the_route_exists_and_a_get_is_still_404(app, client):
    enable(app)
    assert client.get(f"{BASE}/sign-in").status_code == 404
    assert call(client, "sign-in", {"email": "x@y.example", "password": "p"}).status_code == 401  # reaches the handler


# --------------------------------------------------------------- signature

def test_unsigned_and_badly_signed_requests_are_401_and_burn_no_nonce(app, client):
    enable(app)
    path = f"{BASE}/me"
    body = json.dumps({"session_token": "x"}).encode()
    cases = {
        "no header": {},
        "garbage": {"X-Aura-Site-Signature": "nonsense"},
        "wrong secret": {"X-Aura-Site-Signature": header(path, body, secret="z" * 48)},
        "signed for another path": {"X-Aura-Site-Signature": header(f"{BASE}/sign-out", body)},
        "unknown kid": {"X-Aura-Site-Signature": header(path, body, kid="k9")},
        "old timestamp": {"X-Aura-Site-Signature": header(path, body, ts=int(time.time()) - 61)},
        "future timestamp": {"X-Aura-Site-Signature": header(path, body, ts=int(time.time()) + 61)},
        "bad sig chars": {"X-Aura-Site-Signature": header(path, body, sig="Z" * 64)},
        "short nonce": {"X-Aura-Site-Signature": header(path, body, nonce="abc")},
    }
    for name, headers in cases.items():
        response = client.post(path, data=body, headers=headers, content_type="application/json")
        assert response.status_code == 401 and response.get_json() == {"ok": False, "error": "unauthorized"}, name
    tampered = client.post(path, data=body + b" ", headers={"X-Aura-Site-Signature": header(path, body)},
                           content_type="application/json")
    assert tampered.status_code == 401
    assert nonce_count(app) == 0  # a forged request cannot burn a genuine nonce


def test_a_timestamp_inside_the_window_works(app, client):
    enable(app)
    for delta in (-59, 59):
        # allow: inside the window the request reaches the handler (session_invalid), not the gate (unauthorized)
        response = call(client, "me", {"session_token": "x"}, ts=int(time.time()) + delta)
        assert response.status_code == 401 and response.get_json()["error"] == "session_invalid", delta


def test_a_request_can_be_used_only_once(app, client):
    enable(app)
    path = f"{BASE}/me"
    body = json.dumps({"session_token": "x"}).encode()
    headers = {"X-Aura-Site-Signature": header(path, body, nonce="replay-nonce-0123456789")}
    first = client.post(path, data=body, headers=headers, content_type="application/json")
    assert first.get_json() == {"ok": False, "error": "session_invalid"}  # allow: the first got through to the handler
    second = client.post(path, data=body, headers=headers, content_type="application/json")
    assert second.status_code == 401 and second.get_json()["error"] == "unauthorized"
    assert nonce_count(app) == 1


def test_the_api_is_csrf_exempt_and_sets_no_cookie(app, client):
    enable(app)
    response = call(client, "sign-in", {"email": "a@b.example", "password": "x"})
    assert response.status_code == 401 and response.get_json()["error"] == "invalid_credentials"  # not a CSRF 400
    assert "Set-Cookie" not in response.headers and response.headers["Cache-Control"] == "no-store"


def test_secret_rotation_two_keys_work_together_and_a_retired_one_stops(app, client):
    enable(app)
    app.config.update(WEBSITE_ACCOUNT_SECRET_2=SECRET2, WEBSITE_ACCOUNT_KEY_ID_2="k2")
    assert call(client, "me", {"session_token": "x"}).get_json()["error"] == "session_invalid"
    assert call(client, "me", {"session_token": "x"}, secret=SECRET2, kid="k2").get_json()["error"] == "session_invalid"
    # a kid cannot be signed with the other kid's secret
    assert call(client, "me", {"session_token": "x"}, secret=SECRET2, kid="k1").status_code == 401
    app.config.update(WEBSITE_ACCOUNT_SECRET="")  # slot 1 retired
    assert call(client, "me", {"session_token": "x"}).get_json()["error"] == "unauthorized"  # slot 1 is gone
    assert call(client, "me", {"session_token": "x"}, secret=SECRET2, kid="k2").get_json()["error"] == "session_invalid"


def test_oversized_and_non_json_bodies_are_refused_after_the_signature(app, client):
    enable(app)
    path = f"{BASE}/me"
    raw = b"not json"
    response = client.post(path, data=raw, headers={"X-Aura-Site-Signature": header(path, raw)})
    assert response.status_code == 400 and response.get_json()["error"] == "invalid_json"
    arr = b"[1]"
    assert client.post(path, data=arr, headers={"X-Aura-Site-Signature": header(path, arr)}).status_code == 400


# ------------------------------------------------------------------- flows

def test_sign_in_me_change_password_sign_out_end_to_end(app, client):
    enable(app)
    make_active(app)
    signed_in = call(client, "sign-in", {"email": "Owner@Haddad.Example", "password": GOOD, "client_ip": "203.0.113.9",
                                         "user_agent": "Mozilla"})
    assert signed_in.status_code == 200
    data = signed_in.get_json()
    token = data["session_token"]
    assert data["account"]["email"] == "owner@haddad.example" and data["account"]["status"] == "ACTIVE"
    assert data["idle_seconds"] == 3600 and data["absolute_seconds"] == 43200
    me = call(client, "me", {"session_token": token})
    assert me.status_code == 200 and me.get_json()["account"]["language"] == "en"

    bad = call(client, "password/change", {"session_token": token, "current_password": "Wrong-Password-1",
                                           "new_password": "Another-Good-Phrase-3", "client_ip": "203.0.113.9"})
    assert bad.status_code == 401 and bad.get_json()["error"] == "invalid_credentials"
    weak = call(client, "password/change", {"session_token": token, "current_password": GOOD, "new_password": "short",
                                            "client_ip": "203.0.113.9"})
    assert weak.status_code == 422 and weak.get_json() == {"ok": False, "error": "password_policy", "reason": "too_short"}
    changed = call(client, "password/change", {"session_token": token, "current_password": GOOD,
                                               "new_password": "Another-Good-Phrase-3", "client_ip": "203.0.113.9"})
    assert changed.status_code == 200
    new_token = changed.get_json()["session_token"]
    assert call(client, "me", {"session_token": token}).status_code == 401  # the old session died
    assert call(client, "me", {"session_token": new_token}).status_code == 200

    assert call(client, "sign-out", {"session_token": new_token}).status_code == 200
    assert call(client, "me", {"session_token": new_token}).status_code == 401


def test_set_password_through_the_api_activates_the_invited_account(app, client):
    from tests.test_customer_accounts_service import invite, new_customer, token_of

    enable(app)
    with app.app_context():
        raw = token_of(invite(app, new_customer(app), "fresh@shop.example", lang="ar"))
    assert call(client, "sign-in", {"email": "fresh@shop.example", "password": GOOD}).status_code == 401
    weak = call(client, "password/set", {"token": raw, "password": "password123", "client_ip": IP})
    assert weak.status_code == 422 and weak.get_json()["reason"] == "too_common"
    done = call(client, "password/set", {"token": raw, "password": GOOD, "client_ip": IP})
    assert done.status_code == 200 and done.get_json()["account"]["language"] == "ar"
    assert call(client, "password/set", {"token": raw, "password": GOOD, "client_ip": IP}).get_json()["error"] == "token_invalid"
    assert call(client, "sign-in", {"email": "fresh@shop.example", "password": GOOD}).status_code == 200


def test_forgot_password_answers_202_whoever_asks(app, client):
    enable(app)
    make_active(app)
    known = call(client, "password/forgot", {"email": "owner@haddad.example", "client_ip": IP})
    unknown = call(client, "password/forgot", {"email": "nobody@haddad.example", "client_ip": IP})
    junk = call(client, "password/forgot", {"email": "not-an-email", "client_ip": IP})
    assert [r.status_code for r in (known, unknown, junk)] == [202, 202, 202]
    assert known.get_json() == unknown.get_json() == junk.get_json() == {"ok": True}


def test_sign_in_is_rate_limited_per_email_and_address_with_a_retry_after(app, client):
    enable(app)
    make_active(app)
    statuses = [call(client, "sign-in", {"email": "owner@haddad.example", "password": "Wrong-Password-1",
                                         "client_ip": "203.0.113.50"}).status_code for _ in range(6)]
    assert statuses == [401] * 5 + [429]
    locked = call(client, "sign-in", {"email": "owner@haddad.example", "password": GOOD, "client_ip": "203.0.113.50"})
    assert locked.status_code == 429 and int(locked.headers["Retry-After"]) >= 1
    other = call(client, "sign-in", {"email": "owner@haddad.example", "password": GOOD, "client_ip": "203.0.113.51"})
    assert other.status_code == 200


def test_the_client_ip_comes_from_the_signed_body_and_junk_falls_back(app, client):
    from app.customer_accounts import service

    assert service.clean_ip("203.0.113.9") == "203.0.113.9"
    assert service.clean_ip("2001:db8::1") == "2001:db8::1"
    assert service.clean_ip("1.2.3.4, 5.6.7.8") == "unknown" and service.clean_ip(None) == "unknown"


def test_email_confirm_through_the_api(app, client):
    from app.customer_accounts import service
    from app.extensions import db_session
    from app.models.customer_accounts import CustomerAccount
    from tests.test_customer_accounts_service import token_of

    enable(app)
    make_active(app)
    with app.app_context():
        account = db_session.execute(select(CustomerAccount)).scalars().one()
        raw = token_of(service.request_email_change(account, "moved@haddad.example", "moved shop", None))
    done = call(client, "email/confirm", {"token": raw, "client_ip": IP})
    assert done.status_code == 200 and done.get_json()["account"]["email"] == "moved@haddad.example"
    assert call(client, "email/confirm", {"token": raw, "client_ip": IP}).get_json()["error"] == "token_invalid"
