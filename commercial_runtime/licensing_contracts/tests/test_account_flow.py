"""Sign in with an Aura account -- the product client against a FAKE Owner
(new customer flow, 2026-10-31, slice S6).

HOW THIS IS TESTED. The real LicensingClient + the real blueprint run over real
HTTP against fake_owner.FakeOwner, which VERIFIES every device signature and
enforces the S5 contract (one public sign-in failure, lockout with Retry-After,
forced password policy, contract version + hash, device-bound single-use
ticket, TRIAL_REQUIRES_UPDATE). So a client that signs the wrong bytes, retries
a 429, or skips a step fails HERE, not on a customer's till.

WHAT THE ASSERTIONS COUNT. Following ENGINEERING.md 1 (shape 1), a refusal is
never asserted by its status alone: where the point is "the guard ran and Owner
was never asked", the test counts the calls the fake Owner actually received
(`fake.hit_count("activations") == 0`).

Run (one file per process):
    python -m pytest commercial_runtime/licensing_contracts/tests/test_account_flow.py -q
"""
import base64
import hashlib
import json
import logging
import os
import sqlite3
import sys
from pathlib import Path

import pytest
from flask import Flask

from commercial_runtime.licensing_contracts import account_flow, routes as routes_module
from commercial_runtime.licensing_contracts.account_session import AccountSessionStore
from commercial_runtime.licensing_contracts.client import (
    AccountTicket, ActivationCode, LicenseKey, LicensingClient, LicensingClientConfig, NetworkError)
from commercial_runtime.licensing_contracts.device_identity import WindowsDpapiDeviceIdentityProvider
from commercial_runtime.licensing_contracts.routes import make_licensing_blueprint
from commercial_runtime.licensing_contracts.tests.fake_owner import FakeOwner, sha256_hex

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="Uses the real Windows DPAPI device identity provider.")

EMAIL = "Customer@Example.com"
TEMP_PASSWORD = "Temp-" + "Sentinel-7Q4Z"  # pragma: allowlist secret
NEW_PASSWORD = "Brand-new-" + "Sentinel-9K2M"  # pragma: allowlist secret
APP_VERSION = "1.0.0-rc.17"


def _build(tmp_path, fake, *, app_version=APP_VERSION, internal_secret=None):
    anchor = tmp_path / "trust_anchor.json"
    anchor.write_text(json.dumps(fake.trust_anchor_json()), encoding="utf-8")
    flask_app = Flask(__name__)
    flask_app.register_blueprint(make_licensing_blueprint(
        product_code="AURA_RETAIL", platform="WINDOWS", app_version=app_version,
        app_data_dir=str(tmp_path / "appdata"), owner_base_url=fake.base_url,
        verify_tls=True, timeout_seconds=5.0, trust_anchor_path=anchor,
        device_identity_factory=lambda d: WindowsDpapiDeviceIdentityProvider(d),
        internal_shared_secret=internal_secret,
    ))
    return flask_app.test_client()


@pytest.fixture
def fake():
    owner = FakeOwner()
    owner.add_account(EMAIL, TEMP_PASSWORD)
    owner.start()
    yield owner
    owner.stop()


@pytest.fixture
def client(tmp_path, fake):
    return _build(tmp_path, fake)


def _sign_in(client, email=EMAIL, password=TEMP_PASSWORD):
    return client.post("/api/licensing/account/sign-in", json={"email": email, "password": password})


def _through_password_and_contract(client):
    flow = _sign_in(client).get_json()["flow_id"]
    assert client.post("/api/licensing/account/password",
                       json={"flow_id": flow, "new_password": NEW_PASSWORD}).status_code == 200
    assert client.post("/api/licensing/account/contract/accept",
                       json={"flow_id": flow, "agreed": True}).status_code == 200
    return flow


# -- sign in ------------------------------------------------------------------

def test_sign_in_returns_the_view_without_the_ticket(client, fake):
    r = _sign_in(client)
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["flow_id"] and body["password_change_required"] is True
    assert [l["license_public_id"] for l in body["licences"]] == ["lic-1"]
    assert body["licences"][0]["trial"] == {"state": "TRIAL", "ends_on": "2026-11-09"}
    assert body["contract"]["required"] is True and body["contract"]["draft"] is False
    assert body["contract"]["sha256"] == sha256_hex(body["contract"]["body"])
    assert body["seller"] == {"name": "Sami Seller", "phone": "+962780069485"}
    # The ticket stays on this side; the page must never be able to read it.
    assert "tk_" not in json.dumps(body) and "ticket" not in body
    assert fake.bad_signatures == 0, "the client signed bytes the device key did not cover"
    sent = [b for p, b in fake.requests if p.endswith("account-sign-ins")][0]
    assert sent["email"] == EMAIL and sent["product_code"] == "AURA_RETAIL" and sent["platform"] == "WINDOWS"
    assert sent["app_version"] == APP_VERSION and sent["device_public_key"] and sent["nonce"] and sent["timestamp"]


def test_a_wrong_password_and_an_unknown_email_get_the_same_single_failure(client, fake):
    wrong = _sign_in(client, password="not-the-password")
    unknown = _sign_in(client, email="nobody@example.com")
    for r in (wrong, unknown):
        assert r.status_code == 401
        assert r.get_json() == {"reason_code": "ACCOUNT_SIGN_IN_FAILED"}
    assert fake.hit_count("account-sign-ins") == 2     # the check really ran at Owner
    assert fake.bad_signatures == 0


def test_a_malformed_form_is_refused_locally_with_the_same_public_failure(client, fake):
    for bad in ({}, {"email": "no-at-sign", "password": "x"}, {"email": EMAIL, "password": ""}):
        r = client.post("/api/licensing/account/sign-in", json=bad)
        assert r.status_code == 401 and r.get_json() == {"reason_code": "ACCOUNT_SIGN_IN_FAILED"}
    assert fake.hit_count("account-sign-ins") == 0


def test_lockout_is_a_429_with_retry_after_and_is_never_retried_by_the_client(client, fake):
    for _ in range(fake.max_failures):
        assert _sign_in(client, password="wrong").status_code == 401
    before = fake.hit_count("account-sign-ins")
    locked = _sign_in(client)                                   # even the RIGHT password
    assert locked.status_code == 429
    body = locked.get_json()
    assert body["reason_code"] == "RATE_LIMITED" and body["retry_after_seconds"] == 120
    assert locked.headers["Retry-After"] == "120"
    # A retrying client would have hammered the locked account up to 5 times.
    assert fake.hit_count("account-sign-ins") == before + 1


def test_a_tampered_contract_text_is_never_shown(client, fake):
    fake.tamper_sha = True
    r = _sign_in(client)
    assert r.status_code == 502 and r.get_json()["reason_code"] == "CONTRACT_HASH_MISMATCH"
    assert "flow_id" not in r.get_json()


def test_the_draft_watermark_flag_follows_the_servers_lawyer_approval(tmp_path):
    owner = FakeOwner(lawyer_approved=False)
    owner.add_account(EMAIL, TEMP_PASSWORD)
    owner.start()
    try:
        body = _sign_in(_build(tmp_path, owner)).get_json()
        assert body["contract"]["lawyer_approved"] is False and body["contract"]["draft"] is True
    finally:
        owner.stop()


def test_owner_unreachable_is_a_503_not_a_wrong_password(tmp_path, fake):
    c = _build(tmp_path, fake)
    fake.stop()
    r = _sign_in(c)
    assert r.status_code == 503 and r.get_json()["reason_code"] in ("NETWORK_UNAVAILABLE", "REQUEST_TIMED_OUT")


# -- the order of the steps ---------------------------------------------------

def test_activation_is_refused_locally_until_the_password_is_changed(client, fake):
    flow = _sign_in(client).get_json()["flow_id"]
    r = client.post("/api/licensing/account/activate", json={"flow_id": flow})
    assert r.status_code == 409 and r.get_json()["reason_code"] == "PASSWORD_CHANGE_REQUIRED"
    r2 = client.post("/api/licensing/account/contract/accept", json={"flow_id": flow, "agreed": True})
    assert r2.status_code == 409 and r2.get_json()["reason_code"] == "PASSWORD_CHANGE_REQUIRED"
    assert fake.hit_count("activations") == 0 and fake.hit_count("contract-acceptances") == 0


def test_activation_is_refused_locally_until_the_contract_is_accepted(client, fake):
    flow = _sign_in(client).get_json()["flow_id"]
    assert client.post("/api/licensing/account/password",
                       json={"flow_id": flow, "new_password": NEW_PASSWORD}).status_code == 200
    r = client.post("/api/licensing/account/activate", json={"flow_id": flow})
    assert r.status_code == 409 and r.get_json()["reason_code"] == "CONTRACT_ACCEPTANCE_REQUIRED"
    assert fake.hit_count("activations") == 0


def test_the_contract_is_not_accepted_without_an_explicit_agreement(client, fake):
    flow = _sign_in(client).get_json()["flow_id"]
    client.post("/api/licensing/account/password", json={"flow_id": flow, "new_password": NEW_PASSWORD})
    for body in ({"flow_id": flow}, {"flow_id": flow, "agreed": "true"}, {"flow_id": flow, "agreed": False}):
        assert client.post("/api/licensing/account/contract/accept", json=body).status_code == 400
    assert fake.hit_count("contract-acceptances") == 0 and fake.acceptances == []


def test_an_unknown_or_missing_flow_id_is_refused_before_owner_is_asked(client, fake):
    for path in ("password", "contract/accept", "activate"):
        r = client.post(f"/api/licensing/account/{path}", json={"flow_id": "nope", "new_password": NEW_PASSWORD})
        assert r.status_code == 409 and r.get_json()["reason_code"] == "ACCOUNT_FLOW_EXPIRED"
    assert fake.hit_count("activations") == 0 and fake.hit_count("account-password-changes") == 0


def test_password_policy_is_enforced_locally_and_by_owner(client, fake):
    flow = _sign_in(client).get_json()["flow_id"]
    short = client.post("/api/licensing/account/password", json={"flow_id": flow, "new_password": "short"})
    assert short.status_code == 400 and short.get_json()["policy_code"] == "too_short"
    assert fake.hit_count("account-password-changes") == 0          # refused without asking Owner
    same = client.post("/api/licensing/account/password", json={"flow_id": flow, "new_password": TEMP_PASSWORD})
    assert same.status_code == 400 and same.get_json()["reason_code"] == "PASSWORD_POLICY_FAILED"
    assert same.get_json()["policy_code"] == "same_as_old"           # Owner's own verdict is passed through
    assert fake.hit_count("account-password-changes") == 1
    ok = client.post("/api/licensing/account/password", json={"flow_id": flow, "new_password": NEW_PASSWORD})
    assert ok.status_code == 200
    again = client.post("/api/licensing/account/password", json={"flow_id": flow, "new_password": NEW_PASSWORD})
    assert again.status_code == 409 and again.get_json()["reason_code"] == "PASSWORD_CHANGE_NOT_NEEDED"


# -- the whole happy path, and its edges -------------------------------------

def test_full_flow_sign_in_password_contract_activate(client, fake):
    flow = _through_password_and_contract(client)
    assert fake.acceptances and fake.acceptances[0]["version"] == "contract-v1"
    assert fake.acceptances[0]["sha256"] == sha256_hex(fake.contract["body"])
    r = client.post("/api/licensing/account/activate", json={"flow_id": flow})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["result"] == "SUCCESS" and r.get_json()["state"] == "ACTIVE_ONLINE"
    assert fake.activations == [{"kind": "account_ticket", "license_public_id": "lic-1",
                                 "email": EMAIL.lower()}]
    status = client.get("/api/licensing/status").get_json()
    assert status["installation_id"] == r.get_json()["installation_id"]
    # The flow is spent: the ticket cannot be used a second time from this screen.
    again = client.post("/api/licensing/account/activate", json={"flow_id": flow})
    assert again.status_code == 409 and again.get_json()["reason_code"] == "ACCOUNT_FLOW_EXPIRED"
    assert fake.bad_signatures == 0


def test_the_signed_password_change_carries_the_new_password_inside_the_signature(client, fake):
    """The device signature must cover the password fields, or an on-path
    attacker could swap them. FakeOwner verifies the signature over the whole
    body, so a client that signed a body WITHOUT new_password would be 401."""
    flow = _sign_in(client).get_json()["flow_id"]
    assert client.post("/api/licensing/account/password",
                       json={"flow_id": flow, "new_password": NEW_PASSWORD}).status_code == 200
    sent = [b for p, b in fake.requests if p.endswith("account-password-changes")][0]
    assert sent["new_password"] == NEW_PASSWORD and fake.bad_signatures == 0


def test_several_licences_need_a_pick_and_only_a_listed_one_is_sent(tmp_path):
    owner = FakeOwner()
    owner.add_account(EMAIL, TEMP_PASSWORD, licences=[
        {"license_public_id": "lic-A", "product_code": "AURA_RETAIL", "plan_code": "P2",
         "branch_label": "Amman", "trial": {"state": "TRIAL", "ends_on": "2026-11-09"}},
        {"license_public_id": "lic-B", "product_code": "AURA_RETAIL", "plan_code": "P2",
         "branch_label": "Irbid", "trial": {"state": "TRIAL", "ends_on": "2026-11-09"}}])
    owner.start()
    try:
        c = _build(tmp_path, owner)
        flow = _through_password_and_contract(c)
        none = c.post("/api/licensing/account/activate", json={"flow_id": flow})
        foreign = c.post("/api/licensing/account/activate", json={"flow_id": flow, "license_public_id": "lic-OTHER"})
        for r in (none, foreign):
            assert r.status_code == 400 and r.get_json()["reason_code"] == "ACTIVATION_GRANT_INVALID"
        assert owner.hit_count("activations") == 0             # the picker rule ran locally
        ok = c.post("/api/licensing/account/activate", json={"flow_id": flow, "license_public_id": "lic-B"})
        assert ok.status_code == 200 and owner.activations[0]["license_public_id"] == "lic-B"
    finally:
        owner.stop()


def test_an_old_build_is_told_to_update_and_can_still_try_again(tmp_path, fake):
    c = _build(tmp_path, fake, app_version="1.0.0-rc.16")
    flow = _through_password_and_contract(c)
    r = c.post("/api/licensing/account/activate", json={"flow_id": flow})
    assert r.status_code == 400 and r.get_json()["reason_code"] == "TRIAL_REQUIRES_UPDATE"
    assert fake.hit_count("activations") == 1
    assert c.get("/api/licensing/status").get_json()["current_state"] == "NOT_CONFIGURED"


def test_a_refused_ticket_ends_the_flow_and_sends_the_person_back_to_sign_in(client, fake):
    flow = _through_password_and_contract(client)
    for t in fake.tickets.values():
        t["used"] = True                                      # Owner burned it
    r = client.post("/api/licensing/account/activate", json={"flow_id": flow})
    assert r.status_code == 400 and r.get_json()["reason_code"] == "ACTIVATION_GRANT_INVALID"
    again = client.post("/api/licensing/account/activate", json={"flow_id": flow})
    assert again.get_json()["reason_code"] == "ACCOUNT_FLOW_EXPIRED"


def test_flow_entries_expire_and_are_capped(fake):
    now = [0.0]
    store = AccountSessionStore(ttl_seconds=60, max_entries=2, clock=lambda: now[0])
    result = account_flow.normalise_sign_in_response({
        "ticket": "t", "licences": [{"license_public_id": "l", "product_code": "AURA_RETAIL"}],
        "contract": {"required": False}})
    a = store.create(result)
    now[0] = 61
    assert store.get(a) is None                                 # expired
    b, c, d = store.create(result), store.create(result), store.create(result)
    assert store.get(b) is None and store.get(c) and store.get(d)   # oldest dropped at the cap


# -- the activation code, and the typed key still works ------------------------

def test_activation_code_and_licence_key_each_work_and_exactly_one_is_required(client, fake):
    both = client.post("/api/licensing/activate", json={"license_key": "AURA-RETAIL-GOOD", "activation_code": "CODE-GOOD"})
    neither = client.post("/api/licensing/activate", json={})
    assert both.status_code == 400 and neither.status_code == 400
    assert fake.hit_count("activations") == 0
    bad = client.post("/api/licensing/activate", json={"activation_code": "CODE-WRONG"})
    assert bad.status_code == 400 and bad.get_json()["reason_code"] == "ACTIVATION_GRANT_INVALID"
    ok = client.post("/api/licensing/activate", json={"activation_code": "CODE-GOOD"})
    assert ok.status_code == 200 and fake.activations[-1] == {"kind": "activation_code"}


def test_the_typed_licence_key_path_is_unchanged(client, fake):
    ok = client.post("/api/licensing/activate", json={"license_key": "AURA-RETAIL-GOOD"})
    assert ok.status_code == 200 and fake.activations == [{"kind": "license_key"}]


# -- the password is never kept ----------------------------------------------

def _find_sentinels(root: Path, needles) -> list:
    """Every file under `root` (SQLite files included, as raw bytes) that
    contains any needle, as UTF-8 or UTF-16."""
    hits = []
    encoded = []
    for n in needles:
        encoded += [n.encode("utf-8"), n.encode("utf-16-le")]
    for path in root.rglob("*"):
        if path.is_file():
            data = path.read_bytes()
            if any(e in data for e in encoded):
                hits.append(str(path))
    return hits


def test_the_scanner_can_actually_find_a_planted_sentinel(tmp_path):
    """MUTATION-PROOF OF THE HARNESS: a scan that cannot find anything proves
    nothing. Plant the sentinel in a file, in a SQLite row and as UTF-16."""
    (tmp_path / "plain.log").write_text("x " + NEW_PASSWORD + " y", encoding="utf-8")
    db = sqlite3.connect(str(tmp_path / "x.db"))
    db.execute("create table t(v text)")
    db.execute("insert into t values (?)", (TEMP_PASSWORD,))
    db.commit()
    db.close()
    (tmp_path / "wide.bin").write_bytes(NEW_PASSWORD.encode("utf-16-le"))
    found = _find_sentinels(tmp_path, [TEMP_PASSWORD, NEW_PASSWORD])
    assert {Path(f).name for f in found} == {"plain.log", "x.db", "wide.bin"}


def test_neither_password_reaches_a_log_a_database_or_a_file(client, fake, tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    logging.getLogger().setLevel(logging.DEBUG)
    flow = _through_password_and_contract(client)
    assert client.post("/api/licensing/account/activate", json={"flow_id": flow}).status_code == 200
    wrong = _sign_in(client, password=TEMP_PASSWORD + "-nope")          # a failed attempt too
    assert wrong.status_code in (401, 429)
    needles = [TEMP_PASSWORD, NEW_PASSWORD, TEMP_PASSWORD + "-nope"]
    assert _find_sentinels(tmp_path / "appdata", needles) == []
    log_text = "\n".join(r.getMessage() for r in caplog.records) + caplog.text
    for n in needles:
        assert n not in log_text
    # Nor does any answer the page sees carry it back.
    for r in (wrong,):
        assert not any(n in r.get_data(as_text=True) for n in needles)
    events = sqlite3.connect(str(tmp_path / "appdata" / "database" / "subsystems" / "licensing.db"))
    kinds = {row[0] for row in events.execute("select event_type from licensing_events")}
    events.close()
    assert {"ACCOUNT_SIGN_IN_SUCCEEDED", "ACCOUNT_PASSWORD_CHANGED", "CONTRACT_ACCEPTED",
            "ACTIVATION_SUCCEEDED"} <= kinds


# -- credential objects --------------------------------------------------------

class _Resp:
    def __init__(self, status=200, body=None, headers=None):
        self.status_code, self._body, self.headers = status, body, headers or {}

    def json(self):
        if self._body is None:
            raise ValueError("no body")
        return self._body


class _Session:
    def __init__(self, *responses):
        self.queue, self.calls = list(responses), []

    def request(self, method, url, json=None, **kw):
        self.calls.append({"method": method, "url": url, "json": json})
        item = self.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class _Signer:
    def sign(self, b):
        return "sig"

    def get_public_key_b64(self):
        return "pub"


def _lc(session):
    return LicensingClient(LicensingClientConfig(base_url="https://o.example/api/licensing/v1"), session=session,
                           sleep_fn=lambda s: None)


@pytest.mark.parametrize("cred,field", [
    (lambda: AccountTicket("tk", "lic-1"), "account_ticket"),
    (lambda: ActivationCode("CODE-1"), "activation_code"),
    (lambda: LicenseKey("KEY-1"), "license_key"),
])
def test_every_credential_object_is_cleared_after_activate_even_when_it_fails(cred, field):
    for outcome in (_Resp(200, {"result": "SUCCESS"}), NetworkError("NETWORK_UNAVAILABLE", "x")):
        # NetworkError raised by the session is wrapped by the client's retry loop; use a requests error.
        import requests
        session = _Session(_Resp(200, {"result": "SUCCESS"})) if isinstance(outcome, _Resp) else \
            _Session(*[requests.exceptions.ConnectionError("down")] * 5)
        c = cred()
        try:
            _lc(session).activate(product_code="AURA_RETAIL", platform="WINDOWS", app_version="1.0.0-rc.17",
                                  release_channel="rc", installation_id="i", device_public_key_b64="pub",
                                  credential=c, idempotency_key="k", signer=_Signer())
        except NetworkError:
            pass
        body = session.calls[0]["json"]
        assert field in body and body[field] is not None          # it WAS sent
        assert c.body_fields() in ({field: None}, {"account_ticket": None, "license_public_id": None})
        assert "<redacted>" in repr(c)


def test_activate_needs_exactly_one_credential():
    c = _lc(_Session())
    kw = dict(product_code="P", platform="WINDOWS", app_version="v", release_channel=None,
              installation_id="i", device_public_key_b64="p", idempotency_key="k", signer=_Signer())
    with pytest.raises(ValueError):
        c.activate(**kw)
    with pytest.raises(ValueError):
        c.activate(license_key="K", credential=ActivationCode("C"), **kw)


def test_account_calls_are_never_retried_and_clear_the_password():
    import requests
    for status in (503, 500):
        session = _Session(_Resp(status, {"error": "x"}), _Resp(200, {}))
        with pytest.raises(NetworkError) as ei:
            _lc(session).account_sign_in(email="a@b.c", password=TEMP_PASSWORD, product_code="P",
                                         platform="WINDOWS", app_version="v", device_public_key_b64="pub",
                                         signer=_Signer())
        assert ei.value.reason_code == "SERVICE_TEMPORARILY_UNAVAILABLE" and len(session.calls) == 1
    session = _Session(requests.exceptions.ConnectionError("down"), _Resp(200, {}))
    with pytest.raises(NetworkError):
        _lc(session).account_sign_in(email="a@b.c", password="p", product_code="P", platform="WINDOWS",
                                     app_version="v", device_public_key_b64="pub", signer=_Signer())
    assert len(session.calls) == 1


# -- Android: the shared-secret route that checks the raw answer ---------------

def test_android_internal_route_validates_the_raw_answer_and_needs_the_secret(tmp_path, fake):
    c = _build(tmp_path, fake, internal_secret="s3cret")
    raw = {"ticket": "tk_abc", "expires_at": "2026-11-01T00:00:00+00:00", "password_change_required": False,
           "licences": [{"license_public_id": "lic-1", "product_code": "AURA_RETAIL", "plan_code": "P1",
                         "branch_label": "x", "trial": {"state": "TRIAL", "ends_on": "2026-11-09"}}],
           "contract": {"required": True, "version_id": "v1", "language": "ar", "title": "t",
                        "body": "نص", "sha256": sha256_hex("نص"), "lawyer_approved": True},
           "seller": {"name": "S", "phone": "+9627"}}
    url = "/api/licensing/_internal/account-sign-in-response"
    assert c.post(url, json=raw).status_code == 403
    assert c.post(url, json=raw, headers={"X-Aura-Internal-Secret": "wrong"}).status_code == 403
    ok = c.post(url, json=raw, headers={"X-Aura-Internal-Secret": "s3cret"})
    assert ok.status_code == 200 and "tk_abc" not in ok.get_data(as_text=True)
    assert ok.get_json()["contract"]["sha256"] == sha256_hex("نص")
    tampered = json.loads(json.dumps(raw))
    tampered["contract"]["body"] = "نص مختلف"
    bad = c.post(url, json=tampered, headers={"X-Aura-Internal-Secret": "s3cret"})
    assert bad.status_code == 502 and bad.get_json()["reason_code"] == "CONTRACT_HASH_MISMATCH"
    refused = c.post(url, json={"reason_code": "ACCOUNT_SIGN_IN_FAILED"}, headers={"X-Aura-Internal-Secret": "s3cret"})
    assert refused.status_code == 401
    # Not registered at all on Windows (no shared secret configured).
    (tmp_path / "w").mkdir()
    assert _build(tmp_path / "w", fake).post(url, json=raw).status_code == 404


def test_plan_name_from_owner_is_carried_and_older_owners_still_work():
    """rc.19: Owner sends `plan_name` next to `plan_code` (string, or {en, ar}, or plan_name_ar);
    an Owner that sends none yields None and the screens map plan_code locally."""
    from commercial_runtime.licensing_contracts.account_flow import _plan_names, LicenceChoice

    assert _plan_names({"plan_code": "X"}) == (None, None)
    assert _plan_names({"plan_name": "Package 1"}) == ("Package 1", None)
    assert _plan_names({"plan_name": {"en": "Package 1", "ar": "الباقة 1"}}) == ("Package 1", "الباقة 1")
    assert _plan_names({"plan_name": "Package 1", "plan_name_ar": "الباقة 1"}) == ("Package 1", "الباقة 1")
    assert _plan_names({"plan_name": 7, "plan_name_ar": ""}) == (None, None)
    view = LicenceChoice("l", "AURA_RETAIL", "AURA_RETAIL_P1_YEARLY", None, None, None, "Package 1", "الباقة 1").public_view()
    assert view["plan_name"] == "Package 1" and view["plan_name_ar"] == "الباقة 1" and view["plan_code"] == "AURA_RETAIL_P1_YEARLY"
