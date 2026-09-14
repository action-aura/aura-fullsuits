"""Tests for the LAN site relay's pairing flow (`site_relay/pairing.py` +
the `/pair` route it adds to `site_relay/routes.py`), retail schema v30
(`docs/launch-readiness/lan-restaurant-design.md` sec4, "B. Discovery and
pairing: QR to trust").

Two layers are exercised, deliberately kept separate:

  * `PairingCodeStore` in isolation -- issue/consume/expiry/single-use-
    under-concurrency, none of which need Flask, SQLite, or a signature at
    all. These are pure unit tests of the in-memory store itself.
  * `POST /pair` through a REAL Flask test client, against a fixture
    database built by running the REAL migration
    (`products/retail/backend/database/schema.py::_migrate_add_site_relay`)
    -- mirrors `test_site_relay_routes.py`'s own stated discipline exactly
    (real Ed25519 keypairs, real canonical-body signing, a real migration,
    never hand-rolled DDL), because the property under test for the "paired
    device can then push" case IS the wire behaviour end-to-end, not
    something a mock of either side could prove.

Nothing here calls `routes.py`'s `pair()` view function directly for the
same reason `test_site_relay_routes.py` never calls `authenticate()`
directly: the property under test is the HTTP-visible behaviour (status
code, JSON shape, reason code, and -- critically for several tests here --
what did or did not get written to the database), not an internal call
that could pass while the route around it is wired wrong.
"""
import base64
import secrets
import sqlite3
import sys
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from flask import Flask

from commercial_runtime.licensing_contracts.canonical import canonicalize_bytes
from commercial_runtime.sync.site_relay import store
from commercial_runtime.sync.site_relay.pairing import (
    PAIRING_CODE_TTL_SECONDS,
    PairingCodeStore,
    PairingError,
)
from commercial_runtime.sync.site_relay.routes import make_site_relay_blueprint

# Same sys.path insertion as test_site_relay_routes.py -- `database.schema`
# (products/retail/backend/database/schema.py) is not reachable as a dotted
# package from the repo root, and every existing test that calls directly
# into schema.py's own migration functions follows this identical
# convention rather than inventing a second import mechanism for the same
# module.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_BACKEND_DIR = _REPO_ROOT / "products" / "retail" / "backend"
for _p in (str(_REPO_ROOT), str(_BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from database import schema as retail_schema  # noqa: E402

PAIR_PATH = "/api/sync/v1/pair"
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
def pairing_codes():
    return PairingCodeStore()


@pytest.fixture
def app(get_conn, pairing_codes):
    """Pairing ENABLED -- most tests in this file want `/pair` to actually
    do something."""
    flask_app = Flask(__name__)
    flask_app.register_blueprint(
        make_site_relay_blueprint(get_conn=get_conn, pairing_codes=pairing_codes)
    )
    flask_app.testing = True
    return flask_app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def app_no_pairing(get_conn):
    """Pairing NOT configured -- `pairing_codes` left at its `None`
    default, exactly like a caller that never opted in. Used only by the
    PAIRING_NOT_AVAILABLE test."""
    flask_app = Flask(__name__)
    flask_app.register_blueprint(make_site_relay_blueprint(get_conn=get_conn))
    flask_app.testing = True
    return flask_app


@pytest.fixture
def client_no_pairing(app_no_pairing):
    return app_no_pairing.test_client()


# ── helpers (mirrors test_site_relay_routes.py's Device/_now_iso/_new_nonce/
# _make_event -- duplicated here rather than shared via a conftest.py,
# matching this test package's existing convention of self-contained test
# files; there is no conftest.py anywhere under commercial_runtime/sync/
# tests/ today) ──────────────────────────────────────────────────────────

def _now_iso(delta_seconds: float = 0.0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=delta_seconds)).isoformat()


def _new_nonce() -> str:
    return secrets.token_urlsafe(32)


class Device:
    """A fake device holding a REAL Ed25519 keypair -- see
    test_site_relay_routes.py's own `Device` docstring for why signing
    exactly the way `relay_client.py::_signed_body` does matters."""

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
            "nonce": nonce or _new_nonce(),
            **extra,
        }
        canonical_bytes = canonicalize_bytes(body)
        signature = self.private_key.sign(canonical_bytes)
        return {**body, "signature": base64.b64encode(signature).decode("ascii")}


def _make_event(entity_type="category", event_type="create") -> dict:
    return {
        "id": str(uuid.uuid4()),
        "entity_type": entity_type,
        "entity_id": str(uuid.uuid4()),
        "event_type": event_type,
        "payload": {"name": "Widgets"},
        "created_at": _now_iso(),
    }


def _pair_body(code: str, device: Device, *, label=None) -> dict:
    body = {
        "pairing_code": code,
        "installation_id": device.installation_id,
        "device_public_key": device.public_key_b64,
    }
    if label is not None:
        body["label"] = label
    return body


# ── PairingCodeStore in isolation: issue / consume / expiry / single-use ───

def test_issue_then_consume_succeeds_second_consume_of_same_code_raises_invalid():
    """MUTATION-PROVEN (see task report): if `consume` stops removing the
    code from the store, the second `consume()` call below finds it still
    present and succeeds instead of raising -- this test goes RED."""
    pairing_store = PairingCodeStore()
    code = pairing_store.issue()

    pairing_store.consume(code)  # must not raise

    with pytest.raises(PairingError) as exc_info:
        pairing_store.consume(code)
    assert exc_info.value.reason_code == "PAIRING_CODE_INVALID"


def test_expired_code_raises_expired():
    pairing_store = PairingCodeStore()
    issued_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    code = pairing_store.issue(now=issued_at)

    past_ttl = issued_at + timedelta(seconds=PAIRING_CODE_TTL_SECONDS + 1)
    with pytest.raises(PairingError) as exc_info:
        pairing_store.consume(code, now=past_ttl)
    assert exc_info.value.reason_code == "PAIRING_CODE_EXPIRED"


def test_unknown_code_and_already_used_code_raise_the_same_reason():
    """An attacker probing codes must not be able to tell "never existed"
    from "existed but was already spent" -- both collapse to
    PAIRING_CODE_INVALID (see pairing.py's module docstring)."""
    pairing_store = PairingCodeStore()
    code = pairing_store.issue()
    pairing_store.consume(code)  # burn it -- now "already used"

    with pytest.raises(PairingError) as exc_unknown:
        pairing_store.consume("this-code-was-never-issued-at-all")
    with pytest.raises(PairingError) as exc_used:
        pairing_store.consume(code)

    assert exc_unknown.value.reason_code == "PAIRING_CODE_INVALID"
    assert exc_used.value.reason_code == "PAIRING_CODE_INVALID"


def test_consume_is_single_use_under_concurrency():
    """Fires `consume()` for the SAME code from many threads at once and
    asserts exactly one succeeds -- proves the find-and-remove sequence in
    `consume()` is genuinely atomic under the lock, not merely correct in
    single-threaded use."""
    pairing_store = PairingCodeStore()
    code = pairing_store.issue()

    outcomes = []
    outcomes_lock = threading.Lock()

    def attempt():
        try:
            pairing_store.consume(code)
            outcome = "success"
        except PairingError as exc:
            outcome = exc.reason_code
        with outcomes_lock:
            outcomes.append(outcome)

    threads = [threading.Thread(target=attempt) for _ in range(25)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert outcomes.count("success") == 1
    assert outcomes.count("PAIRING_CODE_INVALID") == len(threads) - 1


# ── POST /pair: happy path, and proof the pairing is actually USABLE ──────

def test_pair_route_with_valid_code_stores_the_device(client, get_conn, pairing_codes):
    device = Device()
    code = pairing_codes.issue()

    resp = client.post(PAIR_PATH, json=_pair_body(code, device, label="Tablet 1"))

    assert resp.status_code == 200
    assert resp.get_json() == {"paired": True, "installation_id": device.installation_id}

    conn = get_conn()
    stored = store.lookup_paired_device(conn, device.installation_id)
    conn.close()
    assert stored is not None
    assert stored["device_public_key"] == device.public_key_b64
    assert stored["label"] == "Tablet 1"
    assert stored["revoked_at"] is None


def test_paired_device_can_then_actually_push(client, pairing_codes):
    """THE test that proves pairing produced a USABLE pairing rather than
    just a database row: pair via the route, then make a REAL signed push
    through the same blueprint and assert 200."""
    device = Device()
    code = pairing_codes.issue()
    pair_resp = client.post(PAIR_PATH, json=_pair_body(code, device))
    assert pair_resp.status_code == 200

    push_resp = client.post(PUSH_PATH, json=device.sign_body({"events": [_make_event()]}))

    assert push_resp.status_code == 200
    assert push_resp.get_json() == {"stored": 1, "received": 1}


# ── POST /pair: refusals, and what each one must leave written ────────────

def test_pair_route_without_pairing_codes_configured_returns_not_available(
    client_no_pairing, get_conn
):
    """`pairing_codes=None` (the default) must refuse EVERY request with
    PAIRING_NOT_AVAILABLE, never touching the database -- an install that
    never enables pairing must not expose the endpoint's real behaviour."""
    device = Device()

    resp = client_no_pairing.post(
        PAIR_PATH,
        json={
            "pairing_code": "does-not-matter-pairing-is-off",
            "installation_id": device.installation_id,
            "device_public_key": device.public_key_b64,
        },
    )

    assert resp.status_code == 400
    assert resp.get_json()["reason_code"] == "PAIRING_NOT_AVAILABLE"
    conn = get_conn()
    assert store.lookup_paired_device(conn, device.installation_id) is None
    conn.close()


def test_pair_route_rejects_a_device_public_key_that_is_not_32_bytes(
    client, get_conn, pairing_codes
):
    """MUTATION-PROVEN (see task report): dropping the 32-byte length
    check (while keeping the base64-validity check) lets this malformed
    key through to `store.pair_device` -- this test goes RED."""
    device = Device()
    code = pairing_codes.issue()
    not_32_bytes = base64.b64encode(b"too-short-to-be-an-ed25519-key").decode("ascii")

    resp = client.post(
        PAIR_PATH,
        json={
            "pairing_code": code,
            "installation_id": device.installation_id,
            "device_public_key": not_32_bytes,
        },
    )

    assert resp.status_code == 400
    assert resp.get_json()["reason_code"] == "INVALID_REQUEST"
    conn = get_conn()
    assert store.lookup_paired_device(conn, device.installation_id) is None
    conn.close()


def test_pair_route_with_a_bad_code_writes_nothing(client, get_conn):
    """MUTATION-PROVEN (see task report): reordering the route to call
    `store.pair_device` BEFORE `pairing_codes.consume` turns this test RED
    -- a device row would exist despite the 400. Pins the "consume before
    writing anything" invariant directly, rather than only implying it."""
    device = Device()

    resp = client.post(
        PAIR_PATH,
        json={
            "pairing_code": "not-a-real-pairing-code",
            "installation_id": device.installation_id,
            "device_public_key": device.public_key_b64,
        },
    )

    assert resp.status_code == 400
    assert resp.get_json()["reason_code"] == "PAIRING_CODE_INVALID"
    conn = get_conn()
    assert store.lookup_paired_device(conn, device.installation_id) is None
    conn.close()


# ── POST /pair: re-pairing (new key, and clearing a prior revoke) ─────────

def test_repairing_an_existing_installation_replaces_the_key(client, get_conn, pairing_codes):
    """Re-pairing an installation_id that already exists must succeed and
    REPLACE the stored key -- proven here not just by reading the row back,
    but by proving the OLD key's signature stops verifying while the NEW
    key's signature starts working, which is the property that actually
    matters to a device relying on this pairing."""
    installation_id = str(uuid.uuid4())
    original = Device(installation_id)
    code1 = pairing_codes.issue()
    resp1 = client.post(PAIR_PATH, json=_pair_body(code1, original))
    assert resp1.status_code == 200

    replacement = Device(installation_id)
    code2 = pairing_codes.issue()
    resp2 = client.post(PAIR_PATH, json=_pair_body(code2, replacement))
    assert resp2.status_code == 200

    conn = get_conn()
    stored = store.lookup_paired_device(conn, installation_id)
    conn.close()
    assert stored["device_public_key"] == replacement.public_key_b64

    push_with_new_key = client.post(PUSH_PATH, json=replacement.sign_body({"events": [_make_event()]}))
    assert push_with_new_key.status_code == 200

    push_with_old_key = client.post(PUSH_PATH, json=original.sign_body({"events": [_make_event()]}))
    assert push_with_old_key.status_code == 400
    assert push_with_old_key.get_json()["reason_code"] == "INVALID_SIGNATURE"


def test_repairing_a_revoked_device_clears_the_revoke(client, get_conn, pairing_codes):
    """The operator physically issuing a new code is a deliberate
    un-revoke: a device revoked from this hub's own Settings can push
    again once it is re-paired with a fresh, operator-issued code."""
    device = Device()
    code1 = pairing_codes.issue()
    first = client.post(PAIR_PATH, json=_pair_body(code1, device))
    assert first.status_code == 200

    conn = get_conn()
    store.revoke_device(conn, device.installation_id)
    conn.commit()
    conn.close()

    blocked = client.post(PUSH_PATH, json=device.sign_body({"events": []}))
    assert blocked.status_code == 400
    assert blocked.get_json()["reason_code"] == "INSTALLATION_REVOKED"

    code2 = pairing_codes.issue()
    re_pair = client.post(PAIR_PATH, json=_pair_body(code2, device))
    assert re_pair.status_code == 200

    conn = get_conn()
    stored = store.lookup_paired_device(conn, device.installation_id)
    conn.close()
    assert stored["revoked_at"] is None

    allowed = client.post(PUSH_PATH, json=device.sign_body({"events": []}))
    assert allowed.status_code == 200
