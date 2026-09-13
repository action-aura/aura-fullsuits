"""Tests for the LAN site relay's push/pull routes (`site_relay/auth.py` +
`site_relay/routes.py`), retail schema v30
(`docs/launch-readiness/lan-restaurant-design.md` sec3/sec5).

Exercises the REAL protocol end-to-end -- real Ed25519 keypairs
(`cryptography`), real canonical-body signing (`canonicalize_bytes`, the
exact byte-for-byte routine `relay_client.py::_signed_body` uses), a real
Flask test client, and a fixture database built by running the REAL
migration (`products/retail/backend/database/schema.py::
_migrate_add_site_relay`) rather than hand-rolled DDL, so a drift between
this file's fixture and the real schema can never mask a bug or manufacture
a false failure. Mirrors `test_internal_routes.py`'s "drive the blueprint
through a real Flask test client" convention and `test_relay_client.py`'s
"sign real bodies with a real key" discipline, combined: this is the one
place both halves of the LAN sync wire protocol (client-side signing,
server-side verification) are proven against each other directly, never
through a mock of either side.

Nothing here calls `auth.authenticate` directly -- every test goes through
an HTTP request, because the property under test IS the wire behaviour
(status codes, JSON shapes, which reason code comes back), and a unit test
that called `authenticate()` in-process could not prove any of that.
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
from commercial_runtime.sync.site_relay import store
from commercial_runtime.sync.site_relay.replay import DEFAULT_SKEW_SECONDS
from commercial_runtime.sync.site_relay.routes import make_site_relay_blueprint

# `database.schema` (products/retail/backend/database/schema.py) is not
# reachable as a dotted package from the repo root -- there is no
# products/__init__.py chain making it one. Every existing test that calls
# directly into schema.py's own migration functions follows this identical
# sys.path insertion (see e.g. products/retail/tests/
# retail_v13_identity_columns_migration_test.py), so this mirrors that
# established convention rather than inventing a second import mechanism for
# the same module.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_BACKEND_DIR = _REPO_ROOT / "products" / "retail" / "backend"
for _p in (str(_REPO_ROOT), str(_BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from database import schema as retail_schema  # noqa: E402

PUSH_PATH = "/api/sync/v1/push"
PULL_PATH = "/api/sync/v1/pull"


# ── fixtures ─────────────────────────────────────────────────────────────

@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "retail.db"
    conn = sqlite3.connect(str(path))
    # THE REAL MIGRATION, not hand-rolled CREATE TABLE statements -- see
    # module docstring. `_migrate_add_site_relay` is fully self-contained
    # (CREATE TABLE IF NOT EXISTS only, no dependency on any other retail
    # table -- see that function's own docstring in schema.py), so calling
    # it directly against a bare connection is sufficient and correct; it
    # does not require running the entire `_init_retail`/`ensure_schema_
    # version` chain, which would additionally need `_get_path`/backup-dir
    # plumbing this fixture has no business depending on.
    retail_schema._migrate_add_site_relay(conn)
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def get_conn(db_path):
    def _get_conn():
        conn = sqlite3.connect(str(db_path), timeout=30)
        # store.py's/auth.py's stated contract: setting row_factory is the
        # CALLER's job (see store.py's module docstring). This fixture is
        # the caller for every route registered against it below.
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


# ── helpers ──────────────────────────────────────────────────────────────

def _now_iso(delta_seconds: float = 0.0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=delta_seconds)).isoformat()


def _new_nonce() -> str:
    # Matches relay_client.py's NONCE_LENGTH (32) via secrets.token_urlsafe.
    return secrets.token_urlsafe(32)


class Device:
    """A fake paired device holding a REAL Ed25519 keypair, and knowing how
    to sign a request body EXACTLY the way `relay_client.py::_signed_body`
    does: `canonicalize_bytes(body minus "signature")`, sign, base64-encode
    -- so a test proves this route accepts the actual bytes the real client
    would send, not a shape this test file invented independently."""

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


def _pair(get_conn_fn, device: Device, label=None):
    conn = get_conn_fn()
    store.pair_device(conn, device.installation_id, device_public_key=device.public_key_b64, label=label)
    conn.commit()
    conn.close()


def _revoke(get_conn_fn, device: Device):
    conn = get_conn_fn()
    store.revoke_device(conn, device.installation_id)
    conn.commit()
    conn.close()


def _make_event(entity_type="category", event_type="create"):
    return {
        "id": str(uuid.uuid4()),
        "entity_type": entity_type,
        "entity_id": str(uuid.uuid4()),
        "event_type": event_type,
        "payload": {"name": "Widgets"},
        "created_at": _now_iso(),
    }


def _row_count(get_conn_fn, table: str) -> int:
    conn = get_conn_fn()
    n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608 -- table is a fixed literal at every call site, never user input
    conn.close()
    return n


# ── push: happy path + dedup ─────────────────────────────────────────────

def test_push_two_valid_events_stores_both(client, get_conn):
    device = Device()
    _pair(get_conn, device)
    body = device.sign_body({"events": [_make_event(), _make_event()]})

    resp = client.post(PUSH_PATH, json=body)

    assert resp.status_code == 200
    assert resp.get_json() == {"stored": 2, "received": 2}


def test_pushing_the_same_events_again_with_a_fresh_nonce_dedupes(client, get_conn):
    """Idempotency, not replay protection: a genuinely NEW request (fresh
    nonce/timestamp, freshly signed) carrying event ids the log already has
    must be accepted (200), just with nothing new stored -- this is the
    ordinary "client retried after a dropped response" case `store.
    append_events`'s UNIQUE(id) dedup exists for. Contrast with the REPLAY
    test below, which reuses the SAME nonce and must be REJECTED."""
    device = Device()
    _pair(get_conn, device)
    events = [_make_event(), _make_event()]

    first = client.post(PUSH_PATH, json=device.sign_body({"events": events}))
    assert first.status_code == 200

    second = client.post(PUSH_PATH, json=device.sign_body({"events": events}))  # fresh nonce/timestamp

    assert second.status_code == 200
    assert second.get_json() == {"stored": 0, "received": 2}
    assert _row_count(get_conn, "site_sync_events") == 2


# ── replay / tamper / identity -- the security-critical path ─────────────

def test_replaying_an_identical_push_body_is_rejected_as_nonce_reused(client, get_conn):
    """THE single most important test in this file: resending a previously-
    ACCEPTED request byte-for-byte (same installation_id, same timestamp,
    same nonce, same signature) must be rejected outright. If this ever
    passes with a 200, replay protection is gone -- a captured request could
    be resent forever."""
    device = Device()
    _pair(get_conn, device)
    body = device.sign_body({"events": [_make_event()]})
    first = client.post(PUSH_PATH, json=body)
    assert first.status_code == 200

    replay_resp = client.post(PUSH_PATH, json=body)  # byte-for-byte identical body

    assert replay_resp.status_code == 400
    assert replay_resp.get_json()["reason_code"] == "NONCE_REUSED"


def test_tampering_with_event_payload_after_signing_fails_signature_check(client, get_conn):
    device = Device()
    _pair(get_conn, device)
    body = device.sign_body({"events": [_make_event()]})
    tampered = dict(body)
    tampered_event = dict(body["events"][0])
    tampered_event["payload"] = {"name": "Tampered"}
    tampered["events"] = [tampered_event]
    # `signature` is left completely untouched -- it was computed over the
    # ORIGINAL (untampered) canonical body, so it can no longer match.

    resp = client.post(PUSH_PATH, json=tampered)

    assert resp.status_code == 400
    assert resp.get_json()["reason_code"] == "INVALID_SIGNATURE"


def test_unknown_installation_id_is_rejected(client):
    stranger = Device()  # never paired to this hub at all
    body = stranger.sign_body({"events": []})

    resp = client.post(PUSH_PATH, json=body)

    assert resp.status_code == 400
    assert resp.get_json()["reason_code"] == "INSTALLATION_NOT_FOUND"


def test_revoked_device_with_a_valid_signature_is_rejected(client, get_conn):
    device = Device()
    _pair(get_conn, device)
    _revoke(get_conn, device)
    body = device.sign_body({"events": []})  # genuinely, correctly signed

    resp = client.post(PUSH_PATH, json=body)

    assert resp.status_code == 400
    assert resp.get_json()["reason_code"] == "INSTALLATION_REVOKED"


def test_stale_timestamp_is_rejected(client, get_conn):
    device = Device()
    _pair(get_conn, device)
    stale_timestamp = _now_iso(delta_seconds=-(DEFAULT_SKEW_SECONDS + 60))
    body = device.sign_body({"events": []}, timestamp=stale_timestamp)

    resp = client.post(PUSH_PATH, json=body)

    assert resp.status_code == 400
    assert resp.get_json()["reason_code"] == "TIMESTAMP_OUTSIDE_ALLOWED_WINDOW"


def test_revoked_device_with_a_bad_signature_gets_invalid_signature_not_revoked(client, get_conn):
    """Pins the ORDER of auth.py's steps 6/7, not merely their existence:
    signature verification MUST run before the local revoke check. A
    revoked device that ALSO fails its signature check must get
    INVALID_SIGNATURE, never INSTALLATION_REVOKED -- if the revoke check ran
    first, this endpoint would leak "this installation_id is known to this
    hub, and it has been revoked" to ANY caller who merely guesses or
    observes an installation_id, without that caller ever needing to hold
    the corresponding private key (see auth.py's module docstring, "THE
    REVOKE CHECK COMES AFTER SIGNATURE VERIFICATION"). Mutation-proved by
    hand (see the task report): swapping the two checks in auth.py turns
    THIS test red while `test_revoked_device_with_a_valid_signature_is_
    rejected` above stays green regardless of the order -- that test alone
    cannot catch a swap, which is exactly why this second, harsher case
    exists."""
    device = Device()
    _pair(get_conn, device)
    _revoke(get_conn, device)
    body = device.sign_body({"events": []})
    # A correctly-FORMATTED but WRONG Ed25519 signature: 64 real signature
    # bytes (over a DIFFERENT payload), never a garbage/wrong-length string.
    # This guarantees cryptography's Ed25519PublicKey.verify() raises
    # InvalidSignature (which device_identity.verify_signature catches and
    # turns into a clean `False`) rather than some other, uncaught
    # length/format error.
    wrong_signature = device.private_key.sign(b"not-the-real-payload")
    body["signature"] = base64.b64encode(wrong_signature).decode("ascii")

    resp = client.post(PUSH_PATH, json=body)

    assert resp.status_code == 400
    assert resp.get_json()["reason_code"] == "INVALID_SIGNATURE"


# ── pull: cross-device delivery, self-exclusion, cursor persistence ──────

def test_pull_receives_another_devices_events_in_seq_order(client, get_conn):
    device_a = Device()
    device_b = Device()
    _pair(get_conn, device_a)
    _pair(get_conn, device_b)
    events = [_make_event() for _ in range(3)]
    push_resp = client.post(PUSH_PATH, json=device_b.sign_body({"events": events}))
    assert push_resp.status_code == 200

    pull_resp = client.post(PULL_PATH, json=device_a.sign_body({"since": 0}))

    assert pull_resp.status_code == 200
    payload = pull_resp.get_json()
    assert [e["id"] for e in payload["events"]] == [e["id"] for e in events]
    seqs = [e["seq"] for e in payload["events"]]
    assert seqs == sorted(seqs)
    assert payload["cursor"] == seqs[-1]


def test_pull_excludes_a_devices_own_events_but_still_advances_its_cursor(client, get_conn):
    device_b = Device()
    _pair(get_conn, device_b)
    events = [_make_event() for _ in range(3)]
    push_resp = client.post(PUSH_PATH, json=device_b.sign_body({"events": events}))
    assert push_resp.status_code == 200

    pull_resp = client.post(PULL_PATH, json=device_b.sign_body({"since": 0}))

    assert pull_resp.status_code == 200
    assert pull_resp.get_json()["events"] == []  # self-exclusion: none of B's own events come back

    conn = get_conn()
    cursor_row = conn.execute(
        "SELECT * FROM site_device_cursors WHERE installation_id = ?", (device_b.installation_id,)
    ).fetchone()
    conn.close()
    # The cursor row must exist even though zero rows were returned -- a
    # device catching itself up to a seq with nothing new to receive still
    # needs that fact recorded, or site-log pruning could never consider
    # that seq safe to delete (see store.advance_device_cursor's docstring).
    assert cursor_row is not None


def test_pull_also_accepts_get_with_a_signed_body(client, get_conn):
    """Both GET (Android's shape) and POST (the Windows-frozen-build
    workaround) must reach the same handler -- see routes.py's own comment
    on `methods=["GET", "POST"]` for why both exist."""
    device = Device()
    _pair(get_conn, device)

    resp = client.open(PULL_PATH, method="GET", json=device.sign_body({"since": 0}))

    assert resp.status_code == 200
    assert resp.get_json() == {"events": [], "cursor": 0}


# ── batch limits and the fail-closed malformed-event guarantee ───────────

def test_batch_larger_than_max_push_batch_is_rejected(client, get_conn):
    device = Device()
    _pair(get_conn, device)
    events = [_make_event() for _ in range(store.MAX_PUSH_BATCH + 1)]
    body = device.sign_body({"events": events})

    resp = client.post(PUSH_PATH, json=body)

    assert resp.status_code == 400
    assert resp.get_json()["reason_code"] == "INVALID_BATCH"


def test_one_malformed_event_rejects_the_whole_batch_and_writes_nothing(client, get_conn):
    """THE fail-closed guarantee this route's documented divergence from
    Owner (no quarantine table -- see routes.py's module docstring) rests
    on: a single malformed event anywhere in the batch must reject the
    ENTIRE batch, naming the offending index, and must leave GENUINELY
    NOTHING written -- not even the good events on either side of the bad
    one. Asserting the row count (not just the response shape) is what
    actually pins "nothing written"; see the mutation proof in the task
    report for what happens to this specific assertion if push() is changed
    to insert before it finishes validating."""
    device = Device()
    _pair(get_conn, device)
    malformed_index = 1
    events = [_make_event(), {"id": str(uuid.uuid4())}, _make_event()]  # index 1 is missing required fields
    body = device.sign_body({"events": events})

    resp = client.post(PUSH_PATH, json=body)

    assert resp.status_code == 400
    payload = resp.get_json()
    assert payload["reason_code"] == "INVALID_EVENT"
    assert payload["event_index"] == malformed_index
    assert _row_count(get_conn, "site_sync_events") == 0
