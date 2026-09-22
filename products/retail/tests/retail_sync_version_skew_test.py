"""Aura Retail -- multi-device sync: the schema-version skew guard, full
two-device integration proof (commercial_runtime/sync/sync_service.py's
`local_schema_version_provider`, the version gate inside `_apply_event`, and
`read_outbox`'s `_sender_schema_version` stamping).

Root cause this file exists to close: NOTHING anywhere in sync_service.py,
before this guard existed, ever compared a sender's and a receiver's schema
version. This was found the hard way at retail schema v39 -- three columns
(`products.avg_cost`, `sale_items.unit_cost`, `sales.points_redeemed_amount`)
were put on the wire before every apply branch knew their names, and a
device still running the pre-v39 apply code applied those events anyway,
silently dropping the unknown columns (`p.get(...)` simply never asked for
them) -- no error, no log line, no failing test. That specific instance is
fixed elsewhere in this same change (see retail_sync_cost_basis_convergence_
test.py); this file proves the STRUCTURAL guard that stops the NEXT version
bump from repeating it silently.

commercial_runtime/sync/tests/test_schema_version_skew_guard.py proves the
parser's trust boundary and the gate's four branches at the unit level, in
milliseconds, against a hand-rolled minimal schema. It does NOT, and cannot,
prove the guard survives a REAL push from a REAL Flask app through a REAL
`sync_outbox` row into a SECOND device's own database -- that is what this
file closes, the same division of labour retail_sync_cost_basis_convergence_
test.py already established for the v39 column fix (see that file's own
module docstring, "retail_sync_payload_coverage_test.py proves the payload
DICT... It does not... prove a value survives a full push-then-pull round
trip").

Harness: the InMemoryRelay + bare-SQLite install_b pattern retail_money_
sync_test.py / retail_sync_cost_basis_convergence_test.py already
established (see either file's own module docstring for the full
"AURA_APP_DATA trap" / "Postgres trap" reasoning, not repeated here) --
install A is a real, fully-booted Flask app; install B is a bare temp SQLite
database built straight from `database.schema.init_retail()`, never a
second Flask app. `service_a`/`service_b` below are hand-constructed
`SyncService` instances (identical to every other file using this harness --
the real app's own internal, timer-driven SyncService is never touched by
these tests), which is what makes it possible to pass a DIFFERENT
`local_schema_version_provider` to each side per test.

CRITICAL: exactly ONE pytest process per file (AUDIT-010) -- these test
files pollute each other in a shared pytest process; see products/
run_all_tests.py.

Run:
    pytest products/retail/tests/retail_sync_version_skew_test.py -v
"""
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_syncskew_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402

seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")

import app as _app_module  # noqa: E402

app = _app_module.init_app()  # install A: a real, fully-booted Flask app
app.config["TESTING"] = True

from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402
from commercial_runtime.sync.sync_service import SyncService  # noqa: E402
import database.schema as schema  # noqa: E402
from database.schema import get_retail_conn  # noqa: E402

API = '/api/sub/retail'

# Sentinel distinguishing "no local_schema_version_provider passed to
# SyncService at all" (guard entirely inert for this side -- see __init__'s
# own docstring) from "a provider WAS passed and returns None/garbage"
# (opted in, fails closed). Identical device to the one
# test_schema_version_skew_guard.py already uses at the unit level.
_NOT_WIRED = object()


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


# ── Install A: the real Flask app + its own real database ──────────────────

def _new_admin():
    email = f'syncskew-admin-{uuid.uuid4().hex[:8]}@test.local'
    password = 'SyncSkewPW1'
    company_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, require_password_change) "
        "VALUES (?,?,?,?,?,?,?,0)",
        (str(uuid.uuid4()), company_id, 'EMP-0001', email, hash_password(password), 'admin', 'active'),
    )
    conn.commit()
    conn.close()
    c = app.test_client()
    r = c.post('/api/auth/login', json={'email': email, 'password': password})
    assert r.status_code == 200, r.get_json()
    return c, company_id


def _create_category(c, name=None):
    name = name or f'Skew Category {uuid.uuid4().hex[:6]}'
    r = c.post(f'{API}/categories', json={'name': name, 'description': 'skew-test'})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _single_outbox_event(entity_type, event_type):
    """Reads exactly ONE matching row straight from install A's own
    sync_outbox, in the wire shape read_outbox()/relay.push() both use --
    identical helper to retail_sync_cost_basis_convergence_test.py's own,
    used here only by the garbage-sender-version test, which needs to hand-
    mutate `_sender_schema_version` before pushing (something a real
    service_a.push_once() -- which stamps only a trusted parsed int, never a
    caller-supplied one -- cannot produce)."""
    conn = get_retail_conn()
    row = conn.execute(
        "SELECT id, entity_type, entity_id, event_type, payload, created_at FROM sync_outbox "
        "WHERE entity_type=? AND event_type=? ORDER BY rowid", (entity_type, event_type)
    ).fetchone()
    conn.close()
    assert row is not None, f"no {entity_type}/{event_type} event was queued"
    return {
        "id": row["id"], "entity_type": row["entity_type"], "entity_id": row["entity_id"],
        "event_type": row["event_type"], "payload": json.loads(row["payload"]),
        "created_at": row["created_at"],
    }


# ── The relay double (identical to retail_money_sync_test.py's own) ────────

class InMemoryRelay:
    def __init__(self):
        self.events = []
        self._next_seq = 1

    def push(self, events):
        for ev in events:
            self.events.append(dict(ev, seq=self._next_seq))
            self._next_seq += 1
        return {"stored": len(events), "received": len(events)}

    def pull(self, since):
        pending = [ev for ev in self.events if ev["seq"] > since]
        cursor = pending[-1]["seq"] if pending else since
        return {"events": pending, "cursor": cursor}


@pytest.fixture
def relay():
    return InMemoryRelay()


# ── Install B: a bare temp SQLite DB, no second Flask app ──────────────────

@pytest.fixture
def install_b(tmp_path):
    """Identical technique to retail_money_sync_test.py's own fixture -- see
    that file's module docstring for the full reasoning. Repeated here
    rather than imported, matching this project's own "each test file
    survives the others being deleted" convention."""
    b_root = tmp_path / "install_b"
    b_subsys = b_root / "subsystems"
    b_subsys.mkdir(parents=True, exist_ok=True)

    original_base_dir, original_subsys_dir = schema.BASE_DIR, schema.SUBSYS_DIR
    schema.BASE_DIR = str(b_root)
    schema.SUBSYS_DIR = str(b_subsys)
    try:
        schema.init_retail()
    finally:
        schema.BASE_DIR, schema.SUBSYS_DIR = original_base_dir, original_subsys_dir

    db_path = b_subsys / "retail.db"

    def _get_conn():
        c = sqlite3.connect(str(db_path), timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA busy_timeout=30000")
        c.execute("PRAGMA foreign_keys=ON")
        return c

    return _get_conn


def _service_a(relay, schema_version=_NOT_WIRED):
    kwargs = dict(client_factory=lambda: relay, get_conn=get_retail_conn)
    if schema_version is not _NOT_WIRED:
        kwargs["local_schema_version_provider"] = lambda: schema_version
    return SyncService(**kwargs)


def _service_b(relay, install_b, schema_version=_NOT_WIRED):
    kwargs = dict(client_factory=lambda: relay, get_conn=install_b,
                  local_company_id_provider=lambda: "install-b-company")
    if schema_version is not _NOT_WIRED:
        kwargs["local_schema_version_provider"] = lambda: schema_version
    return SyncService(**kwargs)


@pytest.fixture(autouse=True)
def _clean_install_a_outbox_and_cursor():
    """Install A is booted ONCE at module import time and shared by every
    test below -- there is no per-test database the way `install_b` gives
    each test its own temp directory. `sync_outbox` is device-wide, and
    `sync_cursor` is a single global row -- see retail_changed_field_delta_
    test.py's identical fixture for the full reasoning (every test here
    also builds its own function-scoped `relay` whose seq numbering
    restarts at 1, so a stale non-zero cursor would silently return zero
    events forever)."""
    conn = get_retail_conn()
    conn.execute("DELETE FROM sync_outbox")
    conn.execute("UPDATE sync_cursor SET last_seq=0 WHERE id=1")
    conn.commit()
    conn.close()
    yield
    conn = get_retail_conn()
    conn.execute("DELETE FROM sync_outbox")
    conn.execute("UPDATE sync_cursor SET last_seq=0 WHERE id=1")
    conn.commit()
    conn.close()


def _b_category(install_b, cat_id):
    conn = install_b()
    row = conn.execute("SELECT * FROM categories WHERE id=?", (cat_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def _b_quarantine_rows(install_b):
    conn = install_b()
    rows = [dict(r) for r in conn.execute("SELECT * FROM sync_apply_quarantine").fetchall()]
    conn.close()
    return rows


# ─────────────────────────────────────────────────────────────────────────────
# 1. Same version on both sides: syncs exactly as today (THE REGRESSION RISK)
# ─────────────────────────────────────────────────────────────────────────────

def test_same_schema_version_both_sides_syncs_exactly_as_today(install_b, relay):
    admin, cid = _new_admin()
    cat_id = _create_category(admin, 'Same Version Category')

    service_a = _service_a(relay, schema_version=39)
    service_b = _service_b(relay, install_b, schema_version=39)
    service_a.push_once()
    service_b.pull_once()

    row = _b_category(install_b, cat_id)
    assert row is not None and row['name'] == 'Same Version Category'
    assert _b_quarantine_rows(install_b) == []


def test_neither_side_configured_syncs_exactly_as_today(install_b, relay):
    """Every real call site as of this guard's introduction
    (products/retail/backend/app.py wires neither instance with a
    local_schema_version_provider yet) -- this is that exact shape."""
    admin, cid = _new_admin()
    cat_id = _create_category(admin, 'Unconfigured Category')

    service_a = _service_a(relay)  # not wired
    service_b = _service_b(relay, install_b)  # not wired
    service_a.push_once()
    service_b.pull_once()

    row = _b_category(install_b, cat_id)
    assert row is not None and row['name'] == 'Unconfigured Category'
    assert _b_quarantine_rows(install_b) == []


# ─────────────────────────────────────────────────────────────────────────────
# 2. Older applying newer: refused, loudly, with the reason recorded
# ─────────────────────────────────────────────────────────────────────────────

def test_older_device_applying_a_newer_devices_row_is_refused_loudly(install_b, relay):
    admin, cid = _new_admin()
    cat_id = _create_category(admin, 'Newer Sender Category')

    service_a = _service_a(relay, schema_version=39)   # sender: ahead
    service_b = _service_b(relay, install_b, schema_version=38)  # receiver: behind
    service_a.push_once()
    service_b.pull_once()

    assert _b_category(install_b, cat_id) is None, (
        "an older device applied a newer device's row -- exactly the "
        "v38/v39 hole this guard exists to close"
    )
    quarantined = _b_quarantine_rows(install_b)
    assert len(quarantined) == 1
    assert quarantined[0]['entity_type'] == 'category'
    assert quarantined[0]['entity_id'] == cat_id
    assert quarantined[0]['reason'] == 'schema_version_too_new'
    assert '39' in quarantined[0]['detail'] and '38' in quarantined[0]['detail']

    # Loudly means "an operator could see it" -- get_health() surfaces the
    # SAME count through the existing sync-freshness health dict.
    health = service_b.get_health()
    assert health['schema_version_quarantine_count'] == 1


# ─────────────────────────────────────────────────────────────────────────────
# 3. Newer pulling older: applies normally, per the chosen policy
#    ("usually fine" -- no unknown columns can arrive from an OLDER sender)
# ─────────────────────────────────────────────────────────────────────────────

def test_newer_device_pulling_an_older_devices_row_applies_normally(install_b, relay):
    admin, cid = _new_admin()
    cat_id = _create_category(admin, 'Older Sender Category')

    service_a = _service_a(relay, schema_version=38)   # sender: behind
    service_b = _service_b(relay, install_b, schema_version=39)  # receiver: ahead
    service_a.push_once()
    service_b.pull_once()

    row = _b_category(install_b, cat_id)
    assert row is not None and row['name'] == 'Older Sender Category', (
        "a newer device refused an older device's row -- this direction is "
        "the policy's own 'usually fine' case (no unknown columns can "
        "arrive from an OLDER sender) and must not be gated"
    )
    assert _b_quarantine_rows(install_b) == []


# ─────────────────────────────────────────────────────────────────────────────
# 4. Garbage/absent version: fails closed
# ─────────────────────────────────────────────────────────────────────────────

def test_absent_sender_version_fails_closed_even_though_receiver_is_configured(install_b, relay):
    """Sender not wired with a provider at all (produces no
    `_sender_schema_version` key -- indistinguishable, on purpose, from a
    corrupted one) against an opted-in receiver."""
    admin, cid = _new_admin()
    cat_id = _create_category(admin, 'Unstamped Category')

    service_a = _service_a(relay)  # not wired -- stamps nothing
    service_b = _service_b(relay, install_b, schema_version=38)
    service_a.push_once()
    service_b.pull_once()

    assert _b_category(install_b, cat_id) is None
    quarantined = _b_quarantine_rows(install_b)
    assert len(quarantined) == 1
    assert quarantined[0]['reason'] == 'schema_version_unverifiable'


def test_garbage_sender_version_fails_closed(install_b, relay):
    """A hand-corrupted `_sender_schema_version` (a string, the shape a
    tampered or buggy future emitter might carry) -- a real
    service_a.push_once() can never produce this (it only ever stamps a
    value that already passed `_parse_schema_version`), so this event is
    hand-pushed, mirroring retail_sync_cost_basis_convergence_test.py's own
    `_single_outbox_event` technique."""
    admin, cid = _new_admin()
    cat_id = _create_category(admin, 'Garbage Version Category')

    event = _single_outbox_event("category", "create")
    event["payload"]["_sender_schema_version"] = "not-an-int"
    relay.push([event])

    service_b = _service_b(relay, install_b, schema_version=38)
    service_b.pull_once()

    assert _b_category(install_b, cat_id) is None
    quarantined = _b_quarantine_rows(install_b)
    assert len(quarantined) == 1
    assert quarantined[0]['reason'] == 'schema_version_unverifiable'


def test_garbage_receiver_version_fails_closed(install_b, relay):
    admin, cid = _new_admin()
    cat_id = _create_category(admin, 'Garbage Receiver Category')

    service_a = _service_a(relay, schema_version=38)
    service_b = _service_b(relay, install_b, schema_version="not-an-int")
    service_a.push_once()
    service_b.pull_once()

    assert _b_category(install_b, cat_id) is None
    quarantined = _b_quarantine_rows(install_b)
    assert len(quarantined) == 1
    assert quarantined[0]['reason'] == 'schema_version_unverifiable'


# ─────────────────────────────────────────────────────────────────────────────
# 5. A refusal does not corrupt, half-apply, or lose the events involved
# ─────────────────────────────────────────────────────────────────────────────

def test_a_refusal_leaves_the_database_unchanged_not_merely_raises_no_exception(install_b, relay):
    """"It raised" and "it changed nothing" are different claims (see
    CLAUDE.md's verification-failure-patterns note) -- this asserts the
    SECOND one directly. `read_outbox()` stamps EVERY row in one push_once()
    call with the SAME current sender version, so a single push cannot by
    itself mix a compatible and an incompatible event -- the mix this test
    needs comes from two SEPARATE pushes instead: one while A is still at
    B's own version (applies, and must go on existing afterward), one after
    A is upgraded (refused). That also proves the sharper claim: a LATER
    refusal must not roll back or disturb an EARLIER, already-applied row."""
    admin, cid = _new_admin()
    already_synced_id = _create_category(admin, 'Already Synced')

    service_a = _service_a(relay, schema_version=38)
    service_b = _service_b(relay, install_b, schema_version=38)
    service_a.push_once()
    service_b.pull_once()
    assert _b_category(install_b, already_synced_id) is not None, (
        "setup precondition failed -- the compatible category never landed"
    )

    # A is upgraded to 39 and creates a second category; B is NOT yet
    # upgraded (still 38), so this new event is refused.
    skewed_cat_id = _create_category(admin, 'Refused By Skew Guard')
    service_a_upgraded = _service_a(relay, schema_version=39)
    service_a_upgraded.push_once()

    b_conn = install_b()
    before = [dict(r) for r in b_conn.execute("SELECT id, name FROM categories ORDER BY id").fetchall()]
    b_conn.close()
    assert len(before) == 1, "only the already-synced category should exist on B before this pull"

    # A raised nothing (push/pull with a matching-shaped relay never
    # raises); the interesting claim is what changed, not whether an
    # exception happened -- so no try/except here on purpose.
    service_b.pull_once()

    assert _b_category(install_b, already_synced_id) is not None, (
        "an unrelated LATER refusal must not roll back or disturb an "
        "EARLIER, already-applied row"
    )
    assert _b_category(install_b, skewed_cat_id) is None
    b_conn = install_b()
    after = [dict(r) for r in b_conn.execute("SELECT id, name FROM categories ORDER BY id").fetchall()]
    b_conn.close()
    assert len(after) == 1, "no partial/ghost row for the refused category -- still exactly the one safe row"

    quarantined = _b_quarantine_rows(install_b)
    assert len(quarantined) == 1
    assert quarantined[0]['entity_id'] == skewed_cat_id

    # And the refused event is not LOST -- it is retryable and resolves
    # once this device is upgraded, with no operator action beyond that
    # upgrade (a later ordinary pull tick, even an empty one, retries it).
    service_b_upgraded = _service_b(relay, install_b, schema_version=39)
    service_b_upgraded.pull_once()
    row = _b_category(install_b, skewed_cat_id)
    assert row is not None and row['name'] == 'Refused By Skew Guard'
    assert _b_quarantine_rows(install_b) == []
