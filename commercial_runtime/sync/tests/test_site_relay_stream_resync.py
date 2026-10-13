"""SYNC-BOOT Phase 2, the LAN hub half: per-stream cursors, a recorded prune state,
RESYNC_REQUIRED, include_own, producer requests, and `POST /bootstrap`.

Owner got these in Phase 1 (`owner/tests/test_sync_stream_cursors.py`). The hub is
a second implementation of the same wire contract on raw sqlite3, so it gets its
own proofs rather than an assumption that it behaves the same.

Each class of test has a counter-test. The deny half ("a pull below the pruned
range is refused and writes no cursor") is only worth anything next to the allow
half ("a pull at the boundary is served"): a route that answered 409 to
everything would pass every deny test.

Mutations run against this file (each one turned a named test red; log in the
ROADMAP entry): single per-device cursor in `safe_prune_watermark`; prune
recording the watermark instead of the highest deleted seq; RESYNC check removed;
cursor written on 409; `fast_forward_to` dropped; include_own not echoed;
bootstrap cursor set after instead of before / not set; producer requests sent to
every puller.
"""
import base64
import secrets
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from flask import Flask

from commercial_runtime.licensing_contracts.canonical import canonicalize_bytes
from commercial_runtime.sync.bootstrap.errors import BootstrapError
from commercial_runtime.sync.bootstrap.producer import BootstrapOffer
from commercial_runtime.sync.site_relay import pruning, store
from commercial_runtime.sync.site_relay.routes import make_site_relay_blueprint

_REPO_ROOT = Path(__file__).resolve().parents[3]
_BACKEND_DIR = _REPO_ROOT / "products" / "retail" / "backend"
for _p in (str(_REPO_ROOT), str(_BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from database import schema as retail_schema  # noqa: E402

PULL = "/api/sync/v1/pull"
BOOT = "/api/sync/v1/bootstrap"


class Device:
    def __init__(self):
        self.installation_id = str(uuid.uuid4())
        self.key = Ed25519PrivateKey.generate()

    @property
    def public_key_b64(self):
        raw = self.key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        return base64.b64encode(raw).decode("ascii")

    def sign(self, extra):
        body = {"installation_id": self.installation_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "nonce": secrets.token_urlsafe(32), **extra}
        body["signature"] = base64.b64encode(self.key.sign(canonicalize_bytes(body))).decode("ascii")
        return body


def _event(n=0):
    return {"id": str(uuid.uuid4()), "entity_type": "category", "entity_id": str(uuid.uuid4()),
            "event_type": "create", "payload": {"name": f"c{n}"},
            "created_at": datetime.now(timezone.utc).isoformat()}


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
    def _g():
        c = sqlite3.connect(str(db_path), timeout=30)
        c.row_factory = sqlite3.Row
        return c
    return _g


class Hub:
    def __init__(self, get_conn, **kwargs):
        self.get_conn = get_conn
        self.requested = []
        app = Flask(__name__)
        bp = make_site_relay_blueprint(
            get_conn=get_conn, on_bootstrap_requested=lambda r, s: self.requested.append((r, s)), **kwargs)
        self.board = bp.bootstrap_board
        app.register_blueprint(bp)
        app.testing = True
        self.client = app.test_client()

    def pair(self, device=None):
        device = device or Device()
        c = self.get_conn()
        store.pair_device(c, device.installation_id, device_public_key=device.public_key_b64)
        c.commit()
        c.close()
        return device

    def seed(self, n):
        c = self.get_conn()
        for i in range(n):
            store.append_events(c, [_event(i)], origin_device_id="origin-x")
        c.commit()
        c.close()

    def pull(self, device, since, **extra):
        return self.client.post(PULL, json=device.sign({"since": since, **extra}))

    def cursors(self, device):
        c = self.get_conn()
        rows = {r["stream"]: r["last_seq"] for r in c.execute(
            "SELECT stream, last_seq FROM site_device_cursors WHERE installation_id=?",
            (device.installation_id,))}
        c.close()
        return rows

    def prune_state(self):
        c = self.get_conn()
        try:
            return store.read_prune_state(c)
        finally:
            c.close()


@pytest.fixture
def hub(get_conn):
    return Hub(get_conn)


# ── per-stream cursors ──────────────────────────────────────────────────────

def test_each_stream_has_its_own_cursor_row_and_legacy_is_unchanged(hub):
    dev = hub.pair()
    hub.seed(5)
    assert hub.pull(dev, 0, stream="retail").status_code == 200
    assert hub.cursors(dev) == {"retail": 5}
    assert hub.pull(dev, 0, stream="registry").status_code == 200
    assert hub.cursors(dev) == {"retail": 5, "registry": 5}
    old = hub.pair()
    assert hub.pull(old, 0).status_code == 200            # a client that names no stream
    assert hub.cursors(old) == {"legacy": 5}


def test_a_lagging_stream_holds_the_prune_watermark(hub, get_conn):
    """THE reason for per-stream rows. Retail races ahead, registry has pulled
    nothing: the old single row would have said 5 and let 5 events go."""
    dev = hub.pair()
    hub.seed(5)
    hub.pull(dev, 0, stream="retail")
    c = get_conn()
    c.execute("UPDATE site_forward_cursor SET forwarded_to_seq = 5 WHERE id = 1")
    c.commit()
    assert pruning.safe_prune_watermark(c) == 0           # registry row missing = 0
    hub.pull(dev, 0, stream="registry")
    assert pruning.safe_prune_watermark(c) == 5           # allow half: both streams caught up
    c.close()


def test_the_position_is_the_minimum_of_the_two_streams(get_conn):
    c = get_conn()
    for stream, seq in (("retail", 9), ("registry", 4)):
        store.advance_device_cursor(c, "d1", seq, stream)
    assert store.device_position(c, "d1") == 4
    assert store.read_device_cursor(c, "d1") == 4
    assert store.read_device_cursor(c, "d1", "retail") == 9
    assert store.read_device_cursor(c, "nobody") == 0
    c.close()


def test_the_cursor_never_regresses_per_stream(get_conn):
    c = get_conn()
    store.advance_device_cursor(c, "d1", 9, "retail")
    store.advance_device_cursor(c, "d1", 3, "retail")
    assert store.read_device_cursor(c, "d1", "retail") == 9
    c.close()


def test_an_unknown_stream_or_bad_caps_are_refused_not_coerced(hub):
    dev = hub.pair()
    r = hub.pull(dev, 0, stream="registery")
    assert r.status_code == 400 and r.get_json()["reason_code"] == "INVALID_STREAM"
    r = hub.pull(dev, 0, stream="retail", caps="bootstrap_v1")
    assert r.status_code == 400 and r.get_json()["reason_code"] == "INVALID_CAPS"
    assert hub.cursors(dev) == {}


# ── pruned_through is recorded per real deletion ────────────────────────────

def test_pruned_through_is_the_highest_seq_really_deleted_not_the_watermark(get_conn):
    c = get_conn()
    for i in range(10):
        store.append_events(c, [_event(i)], origin_device_id="o")
    c.commit()
    assert store.read_prune_state(c) == (0, 0)
    # A watermark over nothing deletable records nothing.
    assert pruning.prune_site_log(c, watermark=0) == 0
    assert store.read_prune_state(c) == (0, 0)
    # A batch limit: the watermark says 10 but only 4 rows go -> through 4, not 10.
    assert pruning.prune_site_log(c, watermark=10, batch=4) == 4
    assert store.read_prune_state(c) == (4, 0)
    assert pruning.prune_site_log(c, watermark=10) == 6
    assert store.read_prune_state(c) == (10, 0)
    # Nothing left: a further prune must not move it.
    assert pruning.prune_site_log(c, watermark=10) == 0
    assert store.read_prune_state(c) == (10, 0)
    c.close()


def test_the_prune_and_its_record_commit_together(get_conn):
    c = get_conn()
    store.append_events(c, [_event()], origin_device_id="o")
    c.commit()
    pruning.prune_site_log(c, watermark=1)
    c.rollback()                                           # the caller did not commit
    assert c.execute("SELECT COUNT(*) FROM site_sync_events").fetchone()[0] == 1
    assert store.read_prune_state(c) == (0, 0)
    c.close()


# ── RESYNC_REQUIRED ─────────────────────────────────────────────────────────

def _pruned_hub(hub, get_conn, total=6, through=4):
    hub.seed(total)
    c = get_conn()
    pruning.prune_site_log(c, watermark=through)
    c.commit()
    c.close()


def test_a_pull_below_the_pruned_range_is_409_and_writes_no_cursor(hub, get_conn):
    dev = hub.pair()
    _pruned_hub(hub, get_conn)
    r = hub.pull(dev, 0, stream="retail")
    assert r.status_code == 409
    assert r.get_json() == {"reason_code": "RESYNC_REQUIRED", "pruned_through": 4}
    assert hub.cursors(dev) == {}                          # no row: a position the device does not hold


def test_a_pull_at_the_boundary_is_served(hub, get_conn):
    """Allow half: since == pruned_through means 'I have everything up to 4' and
    events 5 and 6 are all still here."""
    dev = hub.pair()
    _pruned_hub(hub, get_conn)
    r = hub.pull(dev, 4, stream="retail")
    assert r.status_code == 200
    assert [e["seq"] for e in r.get_json()["events"]] == [5, 6]
    assert hub.cursors(dev) == {"retail": 6}


def test_a_log_floor_also_forces_a_resync(hub, get_conn):
    """A hub that started serving a till which already had history: the site log
    is complete only from the floor on, though nothing was ever pruned."""
    dev = hub.pair()
    hub.seed(3)
    c = get_conn()
    store.set_site_log_floor(c, 3)
    c.commit()
    c.close()
    assert hub.prune_state() == (0, 3)
    assert hub.pull(dev, 0, stream="retail").status_code == 409
    assert hub.pull(dev, 3, stream="retail").status_code == 200


def test_the_registry_stream_is_told_where_to_fast_forward_and_the_main_is_asked(hub, get_conn):
    dev = hub.pair()
    _pruned_hub(hub, get_conn)
    reg = hub.pull(dev, 0, stream="registry")
    assert reg.status_code == 409
    assert reg.get_json()["fast_forward_to"] == 6
    assert hub.requested == [(dev.installation_id, "registry")]
    assert hub.board.open_requests()[0]["requester"] == dev.installation_id
    # Retail is told to fetch a copy instead, and does NOT trigger the registry nudge.
    other = hub.pair()
    ret = hub.pull(other, 0, stream="retail")
    assert "fast_forward_to" not in ret.get_json()
    assert (other.installation_id, "registry") not in hub.requested


def test_a_failing_main_callback_never_breaks_the_pull_answer(get_conn):
    def boom(requester, stream):
        raise RuntimeError("main is on fire")
    app = Flask(__name__)
    app.register_blueprint(make_site_relay_blueprint(get_conn=get_conn, on_bootstrap_requested=boom))
    h = Hub.__new__(Hub)
    h.get_conn, h.client, h.requested = get_conn, app.test_client(), []
    dev = Hub.pair(h)
    Hub.seed(h, 3)
    c = get_conn()
    store.set_site_log_floor(c, 3)
    c.commit()
    c.close()
    r = Hub.pull(h, dev, 0, stream="registry")
    assert r.status_code == 409 and r.get_json()["reason_code"] == "RESYNC_REQUIRED"


# ── include_own ─────────────────────────────────────────────────────────────

def test_include_own_returns_the_callers_own_events_once_and_says_so(hub, get_conn):
    dev = hub.pair()
    c = get_conn()
    store.append_events(c, [_event(1), _event(2)], origin_device_id=dev.installation_id)
    store.append_events(c, [_event(3)], origin_device_id="someone-else")
    c.commit()
    c.close()
    plain = hub.pull(dev, 0, stream="retail").get_json()
    assert [e["seq"] for e in plain["events"]] == [3] and "include_own" not in plain
    own = hub.pull(dev, 0, stream="retail", include_own=True).get_json()
    assert [e["seq"] for e in own["events"]] == [1, 2, 3] and own["include_own"] is True


# ── producer requests ride the pull answer ──────────────────────────────────

def test_only_a_producer_capable_puller_is_told_about_open_requests(hub):
    main = hub.pair()
    waiting = hub.pair()
    hub.board.add(waiting.installation_id, "registry")
    quiet = hub.pull(main, 0, stream="retail").get_json()
    assert "bootstrap_requests" not in quiet
    told = hub.pull(main, 0, stream="retail", caps=["bootstrap_producer_v1"]).get_json()
    assert [r["requester"] for r in told["bootstrap_requests"]] == [waiting.installation_id]
    # ...and never its own request back.
    own = hub.pull(waiting, 0, stream="retail", caps=["bootstrap_producer_v1"]).get_json()
    assert "bootstrap_requests" not in own


# ── POST /bootstrap ─────────────────────────────────────────────────────────

class _Provider:
    def __init__(self, offer=None, error=None):
        self.offer, self.error, self.calls = offer, error, []

    def __call__(self, requester, schema_version):
        self.calls.append((requester, schema_version))
        if self.error:
            raise self.error
        return self.offer


def test_bootstrap_is_unavailable_without_a_provider(hub):
    dev = hub.pair()
    r = hub.client.post(BOOT, json=dev.sign({"stream": "retail"}))
    assert r.status_code == 400 and r.get_json()["reason_code"] == "BOOTSTRAP_UNAVAILABLE"


def test_bootstrap_returns_the_copy_and_sets_the_requesters_cursors_first(get_conn):
    provider = _Provider(BootstrapOffer(data=b"ZIPBYTES", as_of=7, registry_fast_forward=9))
    hub = Hub(get_conn, bootstrap_provider=provider)
    dev = hub.pair()
    # A stale HIGHER row from before the device fell behind: the copy is the truth.
    c = get_conn()
    store.advance_device_cursor(c, dev.installation_id, 50, "retail")
    c.commit()
    c.close()
    r = hub.client.post(BOOT, json=dev.sign({"stream": "retail", "schema_version": 56}))
    assert r.status_code == 200 and r.data == b"ZIPBYTES"
    assert r.headers["X-Aura-As-Of"] == "7" and r.headers["X-Aura-Registry-Fast-Forward"] == "9"
    assert provider.calls == [(dev.installation_id, 56)]
    assert hub.cursors(dev) == {"retail": 7, "registry": 9}
    assert hub.requested == [(dev.installation_id, "registry")]       # the main is asked to re-announce


def test_bootstrap_refusals_carry_their_reason_and_set_nothing(get_conn):
    provider = _Provider(error=BootstrapError("PRODUCER_NOT_ON_RELAY", "not on the site log"))
    hub = Hub(get_conn, bootstrap_provider=provider)
    dev = hub.pair()
    r = hub.client.post(BOOT, json=dev.sign({"stream": "retail"}))
    assert r.status_code == 409
    assert r.get_json()["reason_code"] == "PRODUCER_NOT_ON_RELAY"
    assert hub.cursors(dev) == {} and hub.requested == []


def test_bootstrap_needs_a_paired_signed_device_and_burns_the_nonce(get_conn):
    provider = _Provider(BootstrapOffer(data=b"Z", as_of=1, registry_fast_forward=1))
    hub = Hub(get_conn, bootstrap_provider=provider)
    stranger = Device()
    r = hub.client.post(BOOT, json=stranger.sign({"stream": "retail"}))
    assert r.status_code == 400 and r.get_json()["reason_code"] == "INSTALLATION_NOT_FOUND"
    assert provider.calls == []
    dev = hub.pair()
    body = dev.sign({"stream": "retail"})
    assert hub.client.post(BOOT, json=body).status_code == 200
    again = hub.client.post(BOOT, json=body)
    assert again.status_code == 400 and again.get_json()["reason_code"] == "NONCE_REUSED"
    assert len(provider.calls) == 1


def test_bootstrap_only_serves_the_retail_stream(get_conn):
    hub = Hub(get_conn, bootstrap_provider=_Provider(BootstrapOffer(b"Z", 1, 1)))
    dev = hub.pair()
    r = hub.client.post(BOOT, json=dev.sign({"stream": "registry"}))
    assert r.status_code == 400 and r.get_json()["reason_code"] == "INVALID_STREAM"
