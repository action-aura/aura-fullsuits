"""Tests for the Owner-signed site roster (`site_relay/roster.py`) and its
enforcement in the LAN site relay's authentication gate (`site_relay/
auth.py`, step 8), retail schema v30 (`docs/launch-readiness/
lan-restaurant-design.md` sec5, "Layer 3 -- authorization: the Owner-signed
site roster").

Two layers are exercised, deliberately kept separate, mirroring
`test_site_relay_pairing.py`'s own stated split:

  * `roster.py`'s own functions (`verify_roster_envelope`, `store_roster`,
    `load_roster`, `roster_device_status`) tested by DIRECT function call.
    There is no HTTP endpoint for any of these yet -- fetching a roster from
    Owner is the not-yet-built handover documented in `roster.py`'s own
    "THE OWNER ENDPOINT THIS NEEDS" section -- so a real caller here is a
    test standing in for that future fetch job, exactly the way it will one
    day call `store_roster` after fetching an envelope over HTTPS.
  * The ENFORCEMENT gate itself (`auth.py`'s step 8, added by this task)
    tested exclusively through a REAL Flask test client against a fixture
    database built by running the REAL migration
    (`products/retail/backend/database/schema.py::_migrate_add_site_relay`)
    -- mirrors `test_site_relay_routes.py`'s own stated discipline exactly,
    because the property under test there is wire-visible behaviour (status
    code, JSON reason_code), not an internal call that could pass while the
    route around it is wired wrong.

Real Ed25519 keypairs throughout (`cryptography`), real canonical-body
signing (`canonicalize_bytes` -- the identical routine both the licence
assertion and this roster are signed with), and a real `OwnerTrustStore`
built the same way `licensing_contracts/tests/test_assertion_verifier.py`
builds one -- reused, not reinvented, per this task's own instruction to
look at how the licensing tests build a trust store.
"""
import base64
import secrets
import sqlite3
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from flask import Flask

from commercial_runtime.licensing_contracts.canonical import canonicalize_bytes
from commercial_runtime.licensing_contracts.trust_store import OwnerTrustStore
from commercial_runtime.sync.site_relay import roster, store
from commercial_runtime.sync.site_relay.routes import make_site_relay_blueprint

# Same sys.path insertion as test_site_relay_routes.py / test_site_relay_
# pairing.py -- `database.schema` is not reachable as a dotted package from
# the repo root, and every existing test that calls directly into schema.py's
# own migration functions follows this identical convention.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_BACKEND_DIR = _REPO_ROOT / "products" / "retail" / "backend"
for _p in (str(_REPO_ROOT), str(_BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from database import schema as retail_schema  # noqa: E402

PUSH_PATH = "/api/sync/v1/push"


# ── fixtures (mirrors test_site_relay_routes.py's own fixture shapes) ──────

@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "retail.db"
    conn = sqlite3.connect(str(path))
    retail_schema._migrate_add_site_relay(conn)
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def get_conn(db_path):
    def _get_conn():
        conn = sqlite3.connect(str(db_path), timeout=30)
        conn.row_factory = sqlite3.Row
        return conn
    return _get_conn


@pytest.fixture
def app(get_conn):
    flask_app = Flask(__name__)
    flask_app.register_blueprint(make_site_relay_blueprint(get_conn=get_conn))
    flask_app.testing = True
    return flask_app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def owner_key():
    return Ed25519PrivateKey.generate()


@pytest.fixture
def trust_store(tmp_path, owner_key):
    """Built exactly like `licensing_contracts/tests/test_assertion_
    verifier.py`'s own `trust_store` fixture: a real `OwnerTrustStore`
    backed by a temp file, bootstrapped from a one-key anchor naming
    `owner_key` as `"owner-1"`. Reused convention, not a new one -- this
    task's own instruction was to look at how the licensing tests build a
    trust store rather than inventing a second way."""
    s = OwnerTrustStore(tmp_path / "trust.json")
    s.bootstrap_from_anchor(
        {"keys": [{"key_id": "owner-1", "public_key": _b64_pub(owner_key), "algorithm": "ed25519"}]}
    )
    return s


# ── helpers ──────────────────────────────────────────────────────────────

def _b64_pub(private_key) -> str:
    raw = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )
    return base64.b64encode(raw).decode("ascii")


def _now_iso(delta_seconds: float = 0.0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=delta_seconds)).isoformat()


def _device_entry(installation_id: str, status: str, *, platform: str = "WINDOWS") -> dict:
    return {
        "installation_id": installation_id,
        # A placeholder, syntactically-valid base64 key -- roster.py's own
        # device-entry validation (`_validate_devices`) deliberately does
        # NOT require `device_public_key` (see that function's docstring:
        # nothing downstream reads it yet), so its exact value is
        # irrelevant to every test in this file. Present anyway to keep
        # fixture payloads shaped like the real documented contract.
        "device_public_key": base64.b64encode(b"\x00" * 32).decode("ascii"),
        "platform": platform,
        "status": status,
    }


def _roster_payload(*, license_id: str = "lic-1", issued_at: str = None, devices: list = None) -> dict:
    return {
        "license_id": license_id,
        "issued_at": issued_at or _now_iso(),
        "devices": devices if devices is not None else [],
    }


def _sign_roster(signing_key, key_id: str, payload: dict) -> dict:
    """Mirrors `test_assertion_verifier.py`'s own `_envelope` helper --
    the identical envelope shape (`payload`/`signing_key_id`/`algorithm`/
    `signature`), signed over `canonicalize_bytes(payload)`."""
    signature = signing_key.sign(canonicalize_bytes(payload))
    return {
        "payload": payload,
        "signing_key_id": key_id,
        "algorithm": "ed25519",
        "signature": base64.b64encode(signature).decode("ascii"),
    }


class Device:
    """A fake paired device holding a REAL Ed25519 keypair -- see
    `test_site_relay_routes.py`'s own `Device` docstring for why signing
    exactly the way `relay_client.py::_signed_body` does matters. Duplicated
    here rather than imported, matching `test_site_relay_pairing.py`'s own
    stated convention: this test package has no `conftest.py`, and every
    file here is self-contained."""

    def __init__(self, installation_id=None):
        self.installation_id = installation_id or str(uuid.uuid4())
        self.private_key = Ed25519PrivateKey.generate()

    @property
    def public_key_b64(self) -> str:
        raw_public = self.private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return base64.b64encode(raw_public).decode("ascii")

    def sign_body(self, extra: dict, *, timestamp: str = None, nonce: str = None) -> dict:
        body = {
            "installation_id": self.installation_id,
            "timestamp": timestamp or _now_iso(),
            "nonce": nonce or secrets.token_urlsafe(32),
            **extra,
        }
        canonical_bytes = canonicalize_bytes(body)
        signature = self.private_key.sign(canonical_bytes)
        return {**body, "signature": base64.b64encode(signature).decode("ascii")}


def _pair(get_conn_fn, device: Device, label=None):
    conn = get_conn_fn()
    store.pair_device(conn, device.installation_id, device_public_key=device.public_key_b64, label=label)
    conn.commit()
    conn.close()


def _cache_roster(get_conn_fn, trust_store_, owner_key_, payload: dict, *, key_id: str = "owner-1") -> None:
    """Signs `payload` and stores it via `roster.store_roster`, on its own
    connection, committed -- standing in for the (not-yet-built) job that
    will one day fetch a roster from Owner and cache it exactly this way."""
    conn = get_conn_fn()
    roster.store_roster(conn, _sign_roster(owner_key_, key_id, payload), trust_store=trust_store_)
    conn.commit()
    conn.close()


# ── verify_roster_envelope: shape, trust, tamper -────────────────────────

def test_valid_roster_verifies_and_devices_are_readable(owner_key, trust_store):
    payload = _roster_payload(
        devices=[_device_entry("inst-a", "ACTIVE"), _device_entry("inst-b", "SUSPENDED")]
    )
    env = _sign_roster(owner_key, "owner-1", payload)

    verified = roster.verify_roster_envelope(env, trust_store=trust_store)

    assert verified["license_id"] == payload["license_id"]
    assert {d["installation_id"]: d["status"] for d in verified["devices"]} == {
        "inst-a": "ACTIVE",
        "inst-b": "SUSPENDED",
    }


def test_roster_signed_by_untrusted_key_is_rejected(trust_store):
    stranger_key = Ed25519PrivateKey.generate()  # never admitted to trust_store
    env = _sign_roster(stranger_key, "stranger-1", _roster_payload())

    with pytest.raises(roster.RosterError) as exc_info:
        roster.verify_roster_envelope(env, trust_store=trust_store)

    assert exc_info.value.reason_code == "UNKNOWN_SIGNING_KEY"


def test_tampering_with_payload_after_signing_is_rejected(owner_key, trust_store):
    """Flips a device's status ACTIVE -> SUSPENDED post-signature -- the
    exact tamper this whole gate exists to catch: an attacker trying to make
    a suspended device look active (or vice versa) to a hub that already
    holds a genuinely Owner-signed roster."""
    payload = _roster_payload(devices=[_device_entry("inst-a", "ACTIVE")])
    env = _sign_roster(owner_key, "owner-1", payload)
    env["payload"]["devices"][0]["status"] = "SUSPENDED"  # tamper; signature untouched

    with pytest.raises(roster.RosterError) as exc_info:
        roster.verify_roster_envelope(env, trust_store=trust_store)

    assert exc_info.value.reason_code == "ROSTER_VERIFICATION_FAILED"


@pytest.mark.parametrize(
    "bad_devices",
    [
        "not-a-list",
        [{"installation_id": "inst-a"}],  # missing 'status'
        [{"status": "ACTIVE"}],  # missing 'installation_id'
        ["not-a-dict"],
    ],
)
def test_malformed_devices_list_is_rejected(owner_key, trust_store, bad_devices):
    payload = _roster_payload(devices=bad_devices)
    env = _sign_roster(owner_key, "owner-1", payload)

    with pytest.raises(roster.RosterError) as exc_info:
        roster.verify_roster_envelope(env, trust_store=trust_store)

    assert exc_info.value.reason_code == "ROSTER_VERIFICATION_FAILED"


# ── store_roster / load_roster / roster_device_status: the DAO half ──────

def test_store_then_load_round_trips_and_device_status_resolves(get_conn, owner_key, trust_store):
    payload = _roster_payload(
        devices=[_device_entry("inst-a", "ACTIVE"), _device_entry("inst-b", "SUSPENDED")]
    )
    _cache_roster(get_conn, trust_store, owner_key, payload)

    conn = get_conn()
    loaded = roster.load_roster(conn)
    assert loaded == payload

    assert roster.roster_device_status(conn, "inst-a") == "ACTIVE"
    assert roster.roster_device_status(conn, "inst-b") == "SUSPENDED"
    assert roster.roster_device_status(conn, "inst-never-listed") is None
    conn.close()


def test_no_roster_cached_load_and_status_return_none(get_conn):
    conn = get_conn()
    assert roster.load_roster(conn) is None
    assert roster.roster_device_status(conn, "inst-a") is None
    conn.close()


def test_older_roster_does_not_overwrite_a_newer_one(get_conn, owner_key, trust_store):
    """THE REPLAY GUARD. Mutation-proved by hand (see task report): removing
    the `new_issued_at < existing_issued_at` check in `roster.store_roster`
    turns this test RED -- the older, captured roster would silently
    overwrite the newer one and un-suspend `inst-a` again."""
    newer_payload = _roster_payload(issued_at=_now_iso(), devices=[_device_entry("inst-a", "SUSPENDED")])
    older_payload = _roster_payload(
        issued_at=_now_iso(delta_seconds=-3600), devices=[_device_entry("inst-a", "ACTIVE")]
    )
    _cache_roster(get_conn, trust_store, owner_key, newer_payload)

    conn = get_conn()
    with pytest.raises(roster.RosterError) as exc_info:
        roster.store_roster(
            conn, _sign_roster(owner_key, "owner-1", older_payload), trust_store=trust_store
        )
    assert exc_info.value.reason_code == "ROSTER_STALE"
    conn.commit()  # no-op: store_roster raised before writing anything
    conn.close()

    conn = get_conn()
    # The newer roster's data must remain completely untouched.
    assert roster.roster_device_status(conn, "inst-a") == "SUSPENDED"
    conn.close()


def test_roster_with_equal_issued_at_is_accepted_not_treated_as_stale(get_conn, owner_key, trust_store):
    """The replay guard rejects STRICTLY OLDER roosters only -- an
    identically-timestamped re-store (a retried fetch, or the exact same
    roster re-cached) is not a rollback and must succeed."""
    same_issued_at = _now_iso()
    payload = _roster_payload(issued_at=same_issued_at, devices=[_device_entry("inst-a", "ACTIVE")])
    _cache_roster(get_conn, trust_store, owner_key, payload)

    conn = get_conn()
    roster.store_roster(conn, _sign_roster(owner_key, "owner-1", payload), trust_store=trust_store)
    conn.commit()
    conn.close()  # must not raise


# ── the enforcement gate (auth.py step 8), exercised through the real HTTP
# route -- mirrors test_site_relay_routes.py's own "never call authenticate()
# directly" discipline: the property under test is wire-visible behaviour.

def test_suspended_device_per_roster_is_denied_push_while_active_device_still_pushes(
    client, get_conn, owner_key, trust_store
):
    device_active = Device()
    device_suspended = Device()
    _pair(get_conn, device_active)
    _pair(get_conn, device_suspended)
    payload = _roster_payload(
        devices=[
            _device_entry(device_active.installation_id, "ACTIVE"),
            _device_entry(device_suspended.installation_id, "SUSPENDED"),
        ]
    )
    _cache_roster(get_conn, trust_store, owner_key, payload)

    suspended_resp = client.post(PUSH_PATH, json=device_suspended.sign_body({"events": []}))
    assert suspended_resp.status_code == 400
    assert suspended_resp.get_json()["reason_code"] == "INSTALLATION_SUSPENDED"

    active_resp = client.post(PUSH_PATH, json=device_active.sign_body({"events": []}))
    assert active_resp.status_code == 200


@pytest.mark.parametrize("status,expected_reason", [("DEACTIVATED", "INSTALLATION_DEACTIVATED"),
                                                     ("REPLACED", "INSTALLATION_REPLACED")])
def test_other_denied_statuses_use_the_matching_installation_reason_code(
    client, get_conn, owner_key, trust_store, status, expected_reason
):
    """Pins that DEACTIVATED/REPLACED speak Owner's own public reason-code
    vocabulary too (`licensing_contracts/reason_codes.py`'s
    PUBLIC_REASON_CODES), not just SUSPENDED."""
    device = Device()
    _pair(get_conn, device)
    payload = _roster_payload(devices=[_device_entry(device.installation_id, status)])
    _cache_roster(get_conn, trust_store, owner_key, payload)

    resp = client.post(PUSH_PATH, json=device.sign_body({"events": []}))

    assert resp.status_code == 400
    assert resp.get_json()["reason_code"] == expected_reason


def test_paired_device_absent_from_roster_still_pushes_fine(client, get_conn, owner_key, trust_store):
    """THE DELIBERATE DEVIATION FROM lan-restaurant-design.md SEC5, PINNED
    HERE SO IT IS NEVER "FIXED" BACK TO THE STRICTER READING. Design sec5
    says an installation not on the roster should be refused; `auth.py`'s
    step 8 deliberately does NOT implement that (see that module's
    docstring, "THE ROSTER GATE DENIES BY EXPLICIT STATUS ONLY, NEVER BY
    ABSENCE") -- a newly-paired device that simply is not mentioned yet by
    a (possibly stale) cached roster must keep working. MUTATION-PROVED BY
    HAND (see task report): making the gate deny an installation_id that is
    absent from `roster_device_status`'s result (e.g. treating `None` as a
    denial) turns THIS test RED."""
    known_device = Device()
    absent_device = Device()  # paired locally, but never mentioned by the roster below
    _pair(get_conn, known_device)
    _pair(get_conn, absent_device)
    payload = _roster_payload(devices=[_device_entry(known_device.installation_id, "ACTIVE")])
    _cache_roster(get_conn, trust_store, owner_key, payload)

    resp = client.post(PUSH_PATH, json=absent_device.sign_body({"events": []}))

    assert resp.status_code == 200


def test_no_roster_cached_at_all_paired_device_pushes_fine(client, get_conn):
    """No roster has ever been fetched/cached on this hub -- behaviour must
    be completely unchanged from before this task's `auth.py` edit."""
    device = Device()
    _pair(get_conn, device)

    resp = client.post(PUSH_PATH, json=device.sign_body({"events": []}))

    assert resp.status_code == 200


def test_roster_gate_still_behind_signature_verification(client, get_conn, owner_key, trust_store):
    """THE NO-ORACLE PROPERTY, PRESERVED. A SUSPENDED device sending a BAD
    signature must get INVALID_SIGNATURE, never a roster-derived code --
    exactly the same property `auth.py`'s pre-existing
    `test_revoked_device_with_a_bad_signature_gets_invalid_signature_not_
    revoked` pins for the LOCAL revoke check (`test_site_relay_routes.py`).
    MUTATION-PROVED BY HAND (see task report): moving the roster check to
    run BEFORE signature verification in `auth.authenticate` turns this
    test RED -- an unsigned/unauthenticated caller could then learn a real
    installation_id's roster status without ever holding its private key."""
    device = Device()
    _pair(get_conn, device)
    payload = _roster_payload(devices=[_device_entry(device.installation_id, "SUSPENDED")])
    _cache_roster(get_conn, trust_store, owner_key, payload)

    body = device.sign_body({"events": []})
    # A correctly-FORMATTED but WRONG Ed25519 signature -- see
    # test_site_relay_routes.py's identical technique and comment for why
    # this guarantees InvalidSignature rather than some other error shape.
    wrong_signature = device.private_key.sign(b"not-the-real-payload")
    body["signature"] = base64.b64encode(wrong_signature).decode("ascii")

    resp = client.post(PUSH_PATH, json=body)

    assert resp.status_code == 400
    assert resp.get_json()["reason_code"] == "INVALID_SIGNATURE"
