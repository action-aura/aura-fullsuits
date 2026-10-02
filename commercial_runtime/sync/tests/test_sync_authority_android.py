"""AUDIT-033 / S-AUTH, D3 (Android): the outbox page hands Kotlin the bytes to
sign, and what Kotlin signs verifies in `authority.verify_event`.

Android's Python side cannot sign (the key lives in Kotlin). `/_internal/outbox`
therefore ships each authority event with `payload._auth` WITHOUT `sig` plus a
top-level `auth_tbs_b64`. Kotlin signs those bytes, sets `_auth.sig`, drops
`auth_tbs_b64` and pushes. These tests pin the Python half and the contract the
Kotlin half must meet (Kotlin's own proof: AuthoritySigningTest, which reads the
same fixture file).

Run (ONE FILE PER PYTEST PROCESS):
    pytest commercial_runtime/sync/tests/test_sync_authority_android.py -q
"""
import base64
import copy
import json
import sqlite3
import uuid
from pathlib import Path

import pytest
from flask import Flask

from commercial_runtime.licensing_contracts.trust_store import OwnerTrustStore
from commercial_runtime.sync import authority
from commercial_runtime.sync.internal_routes import make_sync_internal_blueprint
from commercial_runtime.sync.sync_service import SyncService
from commercial_runtime.sync.tests import _make_authority_cross_fixture as fixture_gen
from commercial_runtime.sync.tests._signing import SigningWorld
from commercial_runtime.sync.tests.test_internal_routes import (
    SECRET, FakeStateRepository, _SCHEMA, _auth_headers)

FIXTURE = fixture_gen.OUT


def _page(tmp_path, world, device, rows):
    """Run the real outbox route for `device` (prepare-only, as on Android) over
    `rows` = [(entity_type, entity_id, payload)] and return the JSON page."""
    db = tmp_path / f"retail-{uuid.uuid4().hex[:6]}.db"
    c = sqlite3.connect(str(db))
    c.executescript(_SCHEMA)
    for et, eid, payload in rows:
        c.execute("INSERT INTO sync_outbox (id, entity_type, entity_id, event_type, payload) "
                  "VALUES (?,?,?,?,?)", (str(uuid.uuid4()), et, eid, "update", json.dumps(payload)))
    c.commit()
    c.close()

    def get_conn():
        cc = sqlite3.connect(str(db), timeout=30)
        cc.row_factory = sqlite3.Row
        return cc

    svc = SyncService(None, get_conn, lambda: "co", authority_env_provider=world.provider(device))
    app = Flask(__name__)
    app.register_blueprint(make_sync_internal_blueprint(
        sync_service=svc, get_conn=get_conn,
        state_repository=FakeStateRepository("inst-1"), shared_secret=SECRET))
    app.testing = True
    resp = app.test_client().get("/api/sync/_internal/outbox", headers=_auth_headers())
    assert resp.status_code == 200
    return resp.get_json()["events"]


@pytest.fixture
def world(tmp_path):
    return SigningWorld(tmp_path)


def _kotlin_style(event, sig_bytes_b64):
    """What SyncCoordinator does to a page event before pushing it."""
    out = copy.deepcopy(event)
    out["payload"]["_auth"]["sig"] = sig_bytes_b64
    out.pop("auth_tbs_b64")
    return out


def test_page_carries_tbs_for_authority_events_only(tmp_path, world):
    phone = world.new_device(prepare_only=True)
    events = _page(tmp_path, world, phone, [
        ("till_allocation", "a1", {"terminal_uid": phone.dev, "epoch": 3}),
        ("sale", "s1", {"total": 5}),
    ])
    by_type = {e["entity_type"]: e for e in events}
    alloc, sale = by_type["till_allocation"], by_type["sale"]
    assert "auth_tbs_b64" in alloc
    assert "sig" not in alloc["payload"]["_auth"]
    assert base64.b64decode(alloc["auth_tbs_b64"]) == authority.build_tbs(
        alloc, licence=phone.signer.licence, inst=phone.inst, dev=phone.dev)
    # unchanged for everything that is not authority
    assert "auth_tbs_b64" not in sale
    assert "_auth" not in sale["payload"]


def test_an_already_signed_event_gets_no_tbs_hint(tmp_path, world):
    """A signer that CAN sign (desktop shape) already put `sig` in `_auth`; a
    hint would make Kotlin sign a second time and overwrite a good signature."""
    full = world.new_device()
    (event,) = _page(tmp_path, world, full, [("till_allocation", "a1", {"terminal_uid": full.dev})])
    assert event["payload"]["_auth"].get("sig")
    assert "auth_tbs_b64" not in event


def test_a_signature_over_the_page_bytes_verifies_after_the_kotlin_mutation(tmp_path, world):
    phone = world.new_device(prepare_only=True)
    main = world.new_device()
    (event,) = _page(tmp_path, world, phone, [("till_allocation", "a1", {"terminal_uid": phone.dev, "epoch": 3, "r": 2.0})])
    sig = base64.b64encode(phone.key.sign(base64.b64decode(event["auth_tbs_b64"]))).decode()
    pushed = _kotlin_style(event, sig)
    assert "auth_tbs_b64" not in pushed
    verdict = authority.verify_event(pushed, trust_store=world.trust_store, own_licence=world.licence)
    assert isinstance(verdict, authority.VerifiedSigner), verdict
    assert (verdict.dev, verdict.inst) == (phone.dev, phone.inst)
    # it is the PHONE speaking, nobody else
    assert verdict.dev != main.dev


def test_tampering_the_payload_after_tbs_is_refused(tmp_path, world):
    phone = world.new_device(prepare_only=True)
    (event,) = _page(tmp_path, world, phone, [("till_allocation", "a1", {"terminal_uid": phone.dev, "epoch": 3})])
    sig = base64.b64encode(phone.key.sign(base64.b64decode(event["auth_tbs_b64"]))).decode()
    pushed = _kotlin_style(event, sig)
    pushed["payload"]["epoch"] = 99
    verdict = authority.verify_event(pushed, trust_store=world.trust_store, own_licence=world.licence)
    assert isinstance(verdict, authority.Refusal) and verdict.reason == "bad_signature"


def test_an_unsigned_push_is_refused_as_unsigned(tmp_path, world):
    """Signing failure on the phone: Kotlin pushes without sig; receivers park it."""
    phone = world.new_device(prepare_only=True)
    (event,) = _page(tmp_path, world, phone, [("till_allocation", "a1", {"terminal_uid": phone.dev})])
    pushed = copy.deepcopy(event)
    pushed.pop("auth_tbs_b64")
    verdict = authority.verify_event(pushed, trust_store=world.trust_store, own_licence=world.licence)
    assert isinstance(verdict, authority.Refusal)
    assert verdict.reason in ("unsigned_authority_event", "bad_signature")


# ── cross-language fixture ────────────────────────────────────────────────────

def _fixture():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_the_fixture_is_what_python_signs_today():
    fx = _fixture()
    ev = fx["page_event"]
    assert ev["auth_tbs_b64"] == fx["tbs_b64"]
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(fx["device_seed_b64"]))
    assert base64.b64encode(key.sign(base64.b64decode(fx["tbs_b64"]))).decode() == fx["sig_b64"]
    # tbs is recomputed from the page event by today's Python, not trusted from the file
    assert authority.build_tbs(ev, licence=fx["licence"], inst=fx["inst"], dev=fx["dev"]) == base64.b64decode(fx["tbs_b64"])


def test_the_kotlin_signed_fixture_event_verifies_in_python(tmp_path):
    """Kotlin's AuthoritySigningTest asserts its signature over `tbs_b64` with the
    fixture seed equals `sig_b64` (Ed25519 is deterministic), so this event IS
    what Kotlin pushes."""
    fx = _fixture()
    trust = OwnerTrustStore(tmp_path / "trust.json")
    trust.bootstrap_from_anchor({"keys": [{"key_id": "owner-1", "public_key": fx["owner_pub_b64"], "algorithm": "ed25519"}]})
    pushed = _kotlin_style(fx["page_event"], fx["sig_b64"])
    verdict = authority.verify_event(pushed, trust_store=trust, own_licence=fx["licence"])
    assert isinstance(verdict, authority.VerifiedSigner), verdict
    assert verdict.dev == fx["dev"]
    # the numbers Kotlin must not reformat are exactly as sent
    assert pushed["payload"]["ratio"] == 2.0 and pushed["payload"]["epoch"] == 2
    assert pushed["payload"]["big"] == 9007199254740993


def test_a_kotlin_shaped_event_whose_lease_does_not_attest_dev_is_refused(tmp_path):
    """AUDIT-033 fix round (verifier V finding #1), the deny half. The phone's
    event is correctly signed by its own key under a genuine Owner lease -- but
    the lease never named the registry id it claims (an old Owner, or an
    unpinned phone). Receivers must refuse it as `device_id_not_attested`, not
    as a bad signature: signing is fine, the identity is unconfirmed.

    Allow half: `test_the_kotlin_signed_fixture_event_verifies_in_python`
    (same builder, `attest=True`)."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    fx = fixture_gen.build(attest=False)
    key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(fx["device_seed_b64"]))
    assert "registry_device_id" not in fx["page_event"]["payload"]["_auth"]["lease"]["payload"]
    sig = base64.b64encode(key.sign(base64.b64decode(fx["tbs_b64"]))).decode()
    trust = OwnerTrustStore(tmp_path / "trust.json")
    trust.bootstrap_from_anchor({"keys": [{"key_id": "owner-1", "public_key": fx["owner_pub_b64"], "algorithm": "ed25519"}]})
    verdict = authority.verify_event(_kotlin_style(fx["page_event"], sig),
                                     trust_store=trust, own_licence=fx["licence"])
    assert isinstance(verdict, authority.Refusal), verdict
    assert verdict.reason == authority.REASON_DEV_NOT_ATTESTED == "device_id_not_attested"
