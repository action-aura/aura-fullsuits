"""Tests for the automatic, licence-proven join flow (`site_relay/join.py` +
the `GET /identity` / `POST /join` routes it adds to `site_relay/routes.py`),
retail schema v30 (`docs/launch-readiness/lan-restaurant-design.md`).

Mirrors `test_site_relay_roster.py`'s own stated split and conventions
exactly (this task's own instruction was to reuse that file's idiom rather
than inventing a second one):

  * Real Ed25519 keypairs throughout (`cryptography`), real canonical-body
    signing (`canonicalize_bytes` -- the identical routine the licence
    assertion, the roster, and every push/pull request are all signed
    with), and a real `OwnerTrustStore` built the same way
    `licensing_contracts/tests/test_assertion_verifier.py` and
    `test_site_relay_roster.py` both build one.
  * A real Flask test client against a fixture database built by running
    the REAL migration
    (`products/retail/backend/database/schema.py::_migrate_add_site_relay`)
    -- the property under test is wire-visible behaviour (status code, JSON
    reason_code, and what did or did not get written to
    `site_paired_devices`), not an internal call that could pass while the
    route around it is wired wrong. Nothing here calls
    `join.verify_membership` directly for that reason, matching
    `test_site_relay_routes.py`'s / `test_site_relay_pairing.py`'s own
    stated discipline.
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
from commercial_runtime.licensing_contracts.device_identity import fingerprint_of
from commercial_runtime.licensing_contracts.trust_store import OwnerTrustStore
from commercial_runtime.sync.site_relay import join, store
from commercial_runtime.sync.site_relay.routes import make_site_relay_blueprint

# Same sys.path insertion as test_site_relay_routes.py / test_site_relay_
# pairing.py / test_site_relay_roster.py -- `database.schema` is not
# reachable as a dotted package from the repo root, and every existing test
# that calls directly into schema.py's own migration functions follows this
# identical convention.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_BACKEND_DIR = _REPO_ROOT / "products" / "retail" / "backend"
for _p in (str(_REPO_ROOT), str(_BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from database import schema as retail_schema  # noqa: E402

IDENTITY_PATH = "/api/sync/v1/identity"
JOIN_PATH = "/api/sync/v1/join"
PUSH_PATH = "/api/sync/v1/push"

# This hub's own licence, held constant across every test in this file --
# what a device's assertion is compared against to decide "same shop or
# not." A device that wants to join successfully must present an assertion
# naming exactly this value.
HUB_LICENSE_PUBLIC_ID = "lic-hub-shop-1"

_STUB_HUB_DEVICE_PUBLIC_KEY = base64.b64encode(b"\x11" * 32).decode("ascii")
_STUB_HUB_ASSERTION = {
    "payload": {"stub": True},
    "signing_key_id": "owner-1",
    "algorithm": "ed25519",
    "signature": "stub-signature",
}


# ── fixtures (mirrors test_site_relay_roster.py's own fixture shapes) ──────

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
def owner_key():
    return Ed25519PrivateKey.generate()


@pytest.fixture
def trust_store(tmp_path, owner_key):
    """Built exactly like `licensing_contracts/tests/test_assertion_
    verifier.py`'s and `test_site_relay_roster.py`'s own `trust_store`
    fixtures: a real `OwnerTrustStore` backed by a temp file, bootstrapped
    from a one-key anchor naming `owner_key` as `"owner-1"`."""
    s = OwnerTrustStore(tmp_path / "trust.json")
    s.bootstrap_from_anchor(
        {"keys": [{"key_id": "owner-1", "public_key": _b64_pub(owner_key), "algorithm": "ed25519"}]}
    )
    return s


def _build_app(get_conn_fn, **kwargs) -> Flask:
    flask_app = Flask(__name__)
    flask_app.register_blueprint(make_site_relay_blueprint(get_conn=get_conn_fn, **kwargs))
    flask_app.testing = True
    return flask_app


@pytest.fixture
def app(get_conn, trust_store):
    """Every provider wired: `/identity` and `/join` are both live. Most
    tests in this file want exactly this."""
    return _build_app(
        get_conn,
        hub_identity_provider=lambda: join.hub_identity(
            installation_id="hub-inst-1",
            device_public_key=_STUB_HUB_DEVICE_PUBLIC_KEY,
            assertion_envelope=_STUB_HUB_ASSERTION,
        ),
        license_public_id_provider=lambda: HUB_LICENSE_PUBLIC_ID,
        trust_store_provider=lambda: trust_store,
    )


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def app_no_identity(get_conn, trust_store):
    """`hub_identity_provider` left at its `None` default -- `/join` still
    works, but `/identity` must refuse. Used only by the IDENTITY_UNAVAILABLE
    test."""
    return _build_app(
        get_conn,
        license_public_id_provider=lambda: HUB_LICENSE_PUBLIC_ID,
        trust_store_provider=lambda: trust_store,
    )


@pytest.fixture
def client_no_identity(app_no_identity):
    return app_no_identity.test_client()


# ── helpers ──────────────────────────────────────────────────────────────

def _b64_pub(private_key) -> str:
    raw = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )
    return base64.b64encode(raw).decode("ascii")


def _now_iso(delta_seconds: float = 0.0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=delta_seconds)).isoformat()


def _standard_policy() -> dict:
    """Copied from `licensing_contracts/tests/test_assertion_verifier.py`'s
    own `_standard_policy` -- the minimal SAFE `offline_policy` shape
    `verify_assertion` requires every payload to carry (a policy with an
    unsafe `hard_expiry_behavior` is itself rejected, unrelated to anything
    this file tests -- see that module's own `test_unsafe_hard_expiry_
    behavior_rejected`)."""
    return {
        "check_in_interval_seconds": 86400,
        "retry_interval_seconds": 3600,
        "offline_grace_seconds": 1209600,
        "warning_start_seconds": 864000,
        "hard_expiry_behavior": "RESTRICT_COMMERCIAL_FEATURES_PRESERVE_DATA",
        "clock_rollback_tolerance_seconds": 300,
        "assertion_refresh_threshold_seconds": 86400,
    }


def _assertion_payload(*, installation_id: str, device_fingerprint: str, **overrides) -> dict:
    """Mirrors `test_assertion_verifier.py`'s own `_payload` helper -- every
    field `assertion_verifier.ALLOWED_PAYLOAD_FIELDS` allows, populated with
    values that verify cleanly by default. Individual tests override
    exactly the field(s) they want to exercise (`license_public_id` for the
    shop-boundary tests, `expires_at`/`not_before` for the expiry test)."""
    now = datetime.now(timezone.utc)
    base = {
        "assertion_id": "a-" + secrets.token_hex(4),
        "issuer": "aura-owner",
        "product_code": "AURA_RETAIL",
        "license_public_id": HUB_LICENSE_PUBLIC_ID,
        "installation_public_id": installation_id,
        "platform": "WINDOWS",
        "app_version_policy": "1.0.0",
        "release_channel": "stable",
        "issued_at": now.isoformat(),
        "not_before": (now - timedelta(minutes=5)).isoformat(),
        "expires_at": (now + timedelta(days=1)).isoformat(),
        "license_status": "ACTIVE",
        "installation_status": "ACTIVE",
        "subscription_status": "ACTIVE",
        "allowed_device_count": 5,
        "device_key_fingerprint": device_fingerprint,
        "entitlements": {},
        "offline_policy": _standard_policy(),
        "contract_version": "v1",
    }
    base.update(overrides)
    return base


def _sign_assertion(signing_key, key_id: str, payload: dict) -> dict:
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
    """A fake device holding a REAL Ed25519 keypair -- see
    `test_site_relay_routes.py`'s own `Device` docstring for why signing
    exactly the way `relay_client.py::_signed_body` does matters. Duplicated
    here rather than imported, matching this test package's stated
    convention: there is no `conftest.py`, and every file here is
    self-contained."""

    def __init__(self, installation_id=None):
        self.installation_id = installation_id or str(uuid.uuid4())
        self.private_key = Ed25519PrivateKey.generate()

    @property
    def _raw_public_key(self) -> bytes:
        return self.private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
        )

    @property
    def public_key_b64(self) -> str:
        return base64.b64encode(self._raw_public_key).decode("ascii")

    @property
    def fingerprint(self) -> str:
        """The SAME derivation `join.verify_membership` computes over
        whatever public key a `/join` caller presents -- see
        `licensing_contracts/device_identity.py::fingerprint_of`. Used here
        to build a genuinely self-consistent assertion (the normal case) and,
        deliberately, to build an INCONSISTENT one (the attack test)."""
        return fingerprint_of(self._raw_public_key)

    def build_assertion(self, signing_key, *, key_id: str = "owner-1", **overrides) -> dict:
        payload = _assertion_payload(
            installation_id=self.installation_id, device_fingerprint=self.fingerprint, **overrides
        )
        return _sign_assertion(signing_key, key_id, payload)

    def sign_body(self, extra: dict, *, timestamp: str = None, nonce: str = None) -> dict:
        """Identical shape/signing to `test_site_relay_routes.py`'s own
        `Device.sign_body` -- proves a device that just `/join`-ed can
        immediately push using the SAME key it joined with."""
        body = {
            "installation_id": self.installation_id,
            "timestamp": timestamp or _now_iso(),
            "nonce": nonce or secrets.token_urlsafe(32),
            **extra,
        }
        canonical_bytes = canonicalize_bytes(body)
        signature = self.private_key.sign(canonical_bytes)
        return {**body, "signature": base64.b64encode(signature).decode("ascii")}


def _join_body(device: Device, assertion_envelope: dict, *, label=None) -> dict:
    body = {
        "installation_id": device.installation_id,
        "device_public_key": device.public_key_b64,
        "assertion": assertion_envelope,
    }
    if label is not None:
        body["label"] = label
    return body


# ── GET /identity ────────────────────────────────────────────────────────

def test_identity_route_returns_hub_identity(client):
    resp = client.get(IDENTITY_PATH)

    assert resp.status_code == 200
    assert resp.get_json() == {
        "installation_id": "hub-inst-1",
        "device_public_key": _STUB_HUB_DEVICE_PUBLIC_KEY,
        "assertion": _STUB_HUB_ASSERTION,
    }


def test_identity_route_with_no_provider_returns_unavailable(client_no_identity):
    """A hub that has never activated has no assertion to show -- a normal,
    transient state, not an error -- but the route must still refuse rather
    than crash or fabricate a response."""
    resp = client_no_identity.get(IDENTITY_PATH)

    assert resp.status_code == 400
    assert resp.get_json()["reason_code"] == "IDENTITY_UNAVAILABLE"


# ── POST /join: the happy path, and proof the join is actually USABLE ─────

def test_same_license_device_joins_and_can_then_push(client, get_conn, owner_key):
    device = Device()
    assertion = device.build_assertion(owner_key)

    resp = client.post(JOIN_PATH, json=_join_body(device, assertion, label="Tablet A"))

    assert resp.status_code == 200
    assert resp.get_json() == {"joined": True, "installation_id": device.installation_id}

    conn = get_conn()
    stored = store.lookup_paired_device(conn, device.installation_id)
    conn.close()
    assert stored is not None
    assert stored["device_public_key"] == device.public_key_b64
    assert stored["label"] == "Tablet A"
    assert stored["revoked_at"] is None

    push_resp = client.post(PUSH_PATH, json=device.sign_body({"events": []}))
    assert push_resp.status_code == 200


# ── POST /join: the shop boundary -- THE most important test in this file ─

def test_different_license_public_id_is_refused_license_mismatch(client, get_conn, owner_key):
    """THE SHOP BOUNDARY. A device holding a perfectly genuine, correctly
    signed, in-date, self-consistent assertion -- just for a DIFFERENT
    licence -- must never be admitted. MUTATION-PROVEN by hand (see task
    report): removing the `license_public_id` equality check in
    `join.verify_membership` turns this test RED -- a device from a
    completely different shop would be silently admitted onto this hub."""
    device = Device()
    assertion = device.build_assertion(owner_key, license_public_id="lic-some-other-shop")

    resp = client.post(JOIN_PATH, json=_join_body(device, assertion))

    assert resp.status_code == 400
    assert resp.get_json()["reason_code"] == "LICENSE_MISMATCH"

    conn = get_conn()
    assert store.lookup_paired_device(conn, device.installation_id) is None
    conn.close()


# ── POST /join: untrusted signer ───────────────────────────────────────────

def test_assertion_signed_by_untrusted_key_is_refused(client, get_conn):
    stranger_key = Ed25519PrivateKey.generate()  # never admitted to trust_store
    device = Device()
    assertion = device.build_assertion(stranger_key, key_id="stranger-1")

    resp = client.post(JOIN_PATH, json=_join_body(device, assertion))

    assert resp.status_code == 400
    assert resp.get_json()["reason_code"] == "UNKNOWN_SIGNING_KEY"

    conn = get_conn()
    assert store.lookup_paired_device(conn, device.installation_id) is None
    conn.close()


# ── POST /join: the assertion-is-not-secret attack ────────────────────────

def test_valid_assertion_presented_with_a_different_devices_key_is_refused(
    client, get_conn, owner_key
):
    """THE ATTACK the `device_key_fingerprint` check exists to stop. An
    assertion envelope is NOT secret -- it sits in a plain SQLite column
    (`LicenseStateRecord.assertion_envelope_json`) on the device that owns
    it, readable by anything that can read that device's own database.
    Without the fingerprint check, an attacker who obtains victim A's
    genuine, validly-signed, same-licence assertion could present it here
    alongside THEIR OWN public key (device B's) and be admitted as if they
    were device A -- everything else about the assertion (signature,
    licence, claimed installation_id) would still check out.
    MUTATION-PROVEN by hand (see task report): dropping the `expected_
    device_key_fingerprint` comparison from `join.verify_membership`'s call
    into `verify_assertion` turns this test RED -- the attacker would be
    admitted using the victim's genuine assertion."""
    victim = Device()
    attacker = Device()
    victim_assertion = victim.build_assertion(owner_key)

    # The attacker claims to BE the victim's installation_id (so that check
    # alone cannot catch this) but presents THEIR OWN public key alongside
    # the victim's genuine assertion.
    body = {
        "installation_id": victim.installation_id,
        "device_public_key": attacker.public_key_b64,
        "assertion": victim_assertion,
    }

    resp = client.post(JOIN_PATH, json=body)

    assert resp.status_code == 400
    assert resp.get_json()["reason_code"] == "ASSERTION_DEVICE_MISMATCH"

    conn = get_conn()
    assert store.lookup_paired_device(conn, victim.installation_id) is None
    conn.close()


# ── POST /join: the local-revoke asymmetry with /pair ─────────────────────

def test_revoked_device_cannot_rejoin_itself(client, get_conn, owner_key):
    """THE DELIBERATE ASYMMETRY WITH /pair (see routes.py's `/join`
    docstring, step 5). A `/pair` re-join needs a fresh, operator-issued
    code -- itself the operator's consent to re-admit a revoked device.
    `/join` has no operator in the loop, so without this check a locally
    revoked device would silently re-admit ITSELF on its next automatic
    discovery sweep, simply by presenting the same still-perfectly-valid
    assertion it always had. MUTATION-PROVEN by hand (see task report):
    removing (or reordering before verify_membership, so it can never
    observe a genuinely-verified request) the local-revoke check in
    routes.py's `/join` handler turns this test RED."""
    device = Device()
    first_assertion = device.build_assertion(owner_key)

    first = client.post(JOIN_PATH, json=_join_body(device, first_assertion))
    assert first.status_code == 200

    conn = get_conn()
    store.revoke_device(conn, device.installation_id)
    conn.commit()
    conn.close()

    # A brand-new, still perfectly valid and self-consistent assertion --
    # revocation is not about the assertion being bad, it is about THIS HUB
    # having locally decided not to trust this installation_id any more.
    second_assertion = device.build_assertion(owner_key)
    second = client.post(JOIN_PATH, json=_join_body(device, second_assertion))

    assert second.status_code == 400
    assert second.get_json()["reason_code"] == "INSTALLATION_REVOKED"

    conn = get_conn()
    stored = store.lookup_paired_device(conn, device.installation_id)
    conn.close()
    assert stored["revoked_at"] is not None  # must still be revoked


# ── POST /join: fail-closed when not configured ───────────────────────────

@pytest.mark.parametrize(
    "missing_provider",
    ["trust_store_provider", "license_public_id_provider", "both"],
)
def test_join_unavailable_when_a_required_provider_is_missing_writes_nothing(
    get_conn, trust_store, missing_provider
):
    """`/join` must refuse EVERY request with `JOIN_UNAVAILABLE` -- never
    attempting verification, never touching the database -- whenever either
    of its two required providers was never wired up, exactly like
    `pairing_codes=None` already does for `/pair`. Covers `trust_store_
    provider` missing alone, `license_public_id_provider` missing alone,
    and both missing together."""
    kwargs = {}
    if missing_provider == "trust_store_provider":
        kwargs["license_public_id_provider"] = lambda: HUB_LICENSE_PUBLIC_ID
    elif missing_provider == "license_public_id_provider":
        kwargs["trust_store_provider"] = lambda: trust_store
    # "both": neither kwarg set -- both providers stay at their None default.

    app = _build_app(get_conn, **kwargs)
    test_client = app.test_client()

    device = Device()
    owner_key = Ed25519PrivateKey.generate()
    assertion = device.build_assertion(owner_key)

    resp = test_client.post(JOIN_PATH, json=_join_body(device, assertion))

    assert resp.status_code == 400
    assert resp.get_json()["reason_code"] == "JOIN_UNAVAILABLE"

    conn = get_conn()
    assert store.lookup_paired_device(conn, device.installation_id) is None
    conn.close()


# ── POST /join: expiry ─────────────────────────────────────────────────────

def test_expired_assertion_is_refused(client, get_conn, owner_key):
    device = Device()
    now = datetime.now(timezone.utc)
    assertion = device.build_assertion(
        owner_key,
        not_before=(now - timedelta(days=2)).isoformat(),
        expires_at=(now - timedelta(days=1)).isoformat(),
    )

    resp = client.post(JOIN_PATH, json=_join_body(device, assertion))

    assert resp.status_code == 400
    assert resp.get_json()["reason_code"] == "ASSERTION_EXPIRED"

    conn = get_conn()
    assert store.lookup_paired_device(conn, device.installation_id) is None
    conn.close()
