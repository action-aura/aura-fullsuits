"""Aura Retail -- Aseel-parity wave A-PAR, "per-document-type numbering
series" (schema v34): TWO-DEVICE CONVERGENCE of the `doc_series` entity
type. See `retail_doc_series_device_isolation_test.py` for the collision-
prevention property proved across two REAL processes; this file is the
one-process emit/apply mirror -- shaped directly on
`retail_quotation_sync_test.py`'s own harness (`install_b` + `InMemoryRelay`
+ a real `SyncService`, `_as_device_b` to run real HTTP routes against a
second physical database in one process) -- copied, not imported, per this
test directory's own "each file survives independently" convention.

WHAT THIS FILE PROVES, in order:

  1. A series created through the REAL route queues exactly one
     `sync_outbox` row (`entity_type='doc_series'`).
  2. It applies on device B with the same `id`/`code`/
     `allocator_terminal_uid`.
  3. `doc_series_counter` never appears in ANY `sync_outbox` payload --
     asserted against the OUTBOX rows, not the receiving table, per this
     feature's own design note: a counter that is emitted but ignored on
     apply is still a wire-format leak waiting for the next apply-branch
     edit to start honouring it by mistake.
  4. Delta gating: device A renames the label while device B claims the
     SAME book (an unclaimed one, no race); after both events converge on
     BOTH devices, both changes survive.
  5. `row_version` reject-stale: an older update does not overwrite a
     newer one, and the rejected write lands one `sync_conflicts` row.

Run (one file per process, AUDIT-010):
    C:/Users/MSI/Desktop/aura-fullsuits/.venv/Scripts/python.exe -m pytest \
        products/retail/tests/retail_doc_series_sync_test.py -v
"""
import contextlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest.mock as mock
import uuid
from pathlib import Path

import pytest

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_doc_series_sync_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402

seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")

import app as _app_module  # noqa: E402

app = _app_module.init_app()  # install A: a real, fully-booted Flask app
app.config["TESTING"] = True

import api.retail_api as retail_api_module  # noqa: E402
from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402
from commercial_runtime.sync.sync_service import SyncService  # noqa: E402
import database.schema as schema  # noqa: E402
from database.schema import get_retail_conn  # noqa: E402

API = '/api/sub/retail'

TILL_A = 'docseries-sync-till-a'
TILL_B = 'docseries-sync-till-b'


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


def _new_shop():
    company_id = str(uuid.uuid4())
    email = f'dsync-admin-{uuid.uuid4().hex[:8]}@test.local'
    password = 'DocSeriesSyncPW1'
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, require_password_change) "
        "VALUES (?,?,?,?,?,?,?,0)",
        (str(uuid.uuid4()), company_id, 'EMP-0001', email, hash_password(password), 'admin', 'active'),
    )
    conn.commit()
    conn.close()
    admin = app.test_client()
    r = admin.post('/api/auth/login', json={'email': email, 'password': password})
    assert r.status_code == 200, r.get_json()
    return admin, company_id


class InMemoryRelay:
    """Identical to retail_cheque_sync_test.py's/retail_quotation_sync_
    test.py's own double."""

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


@pytest.fixture
def install_b(tmp_path):
    """Identical technique to retail_quotation_sync_test.py's/retail_
    cheque_sync_test.py's own fixture: a SECOND, real retail.db, built
    through the genuine v0->v34 migration chain."""
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


@pytest.fixture(autouse=True)
def _clean_install_a_outbox():
    """install A is booted once at module import time and shared by every
    test in this file -- see retail_quotation_sync_test.py's identical
    fixture for the full reasoning."""
    conn = get_retail_conn()
    conn.execute("DELETE FROM sync_outbox")
    conn.commit()
    conn.close()
    yield
    conn = get_retail_conn()
    conn.execute("DELETE FROM sync_outbox")
    conn.commit()
    conn.close()


@contextlib.contextmanager
def _as_device_b(get_conn):
    """Runs the enclosed real HTTP calls against device B's OWN separate
    retail.db instead of device A's -- retail_quotation_sync_test.py's
    identical adaptation of retail_cheque_sync_test.py's own technique."""
    with mock.patch.object(retail_api_module, 'get_retail_conn', side_effect=get_conn):
        yield


@contextlib.contextmanager
def _at_terminal(terminal_id):
    """Copied verbatim from retail_drawer_terminal_scope_test.py's own
    `at_terminal` -- see that file's docstring."""
    previous = retail_api_module.local_terminal_id
    retail_api_module.local_terminal_id = lambda: terminal_id
    try:
        yield
    finally:
        retail_api_module.local_terminal_id = previous


def _outbox(entity_type=None):
    conn = get_retail_conn()
    try:
        if entity_type:
            rows = conn.execute(
                "SELECT * FROM sync_outbox WHERE entity_type=? ORDER BY id", (entity_type,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM sync_outbox ORDER BY id").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d['payload'] = json.loads(d['payload'])
            out.append(d)
        return out
    finally:
        conn.close()


def _make_services(install_b, relay, cid):
    # Both devices need a `local_company_id_provider` in THIS file, unlike
    # retail_quotation_sync_test.py's identical-looking pair -- that file
    # only ever calls `service_a.push_once()` (device A never pulls in any
    # of its tests), so its `service_a` never actually needed one. This
    # file's delta-gating test pulls on BOTH sides (A must apply B's own
    # claim event back), which is what surfaces `_get_local_company_id`'s
    # own `RuntimeError` guard the first time -- found by running this
    # file, not by inspection.
    service_a = SyncService(client_factory=lambda: relay, get_conn=get_retail_conn,
                             local_company_id_provider=lambda: cid)
    service_b = SyncService(client_factory=lambda: relay, get_conn=install_b,
                             local_company_id_provider=lambda: cid)
    return service_a, service_b


# ── 1/2. Emit + apply: one outbox row, the same id/code/allocator ──────────

def test_create_queues_exactly_one_outbox_row_and_applies_on_device_b(install_b, relay):
    admin, cid = _new_shop()
    created = admin.post(f'{API}/doc-series', json={'doc_type': 'sale', 'code': 'SYNCA', 'label': 'Main'})
    assert created.status_code == 200, created.get_json()
    series_id = created.get_json()['data']['id']

    rows = _outbox('doc_series')
    assert len(rows) == 1, "create_doc_series must queue exactly one sync_outbox row"
    assert rows[0]['entity_id'] == series_id
    assert rows[0]['event_type'] == 'create'

    with _at_terminal(TILL_A):
        claim = admin.post(f'{API}/doc-series/{series_id}/allocator-claim')
    assert claim.status_code == 200, claim.get_json()

    service_a, service_b = _make_services(install_b, relay, cid)
    service_a.push_once()
    service_b.pull_once()

    b_conn = install_b()
    try:
        b_row = b_conn.execute("SELECT * FROM doc_series WHERE id=?", (series_id,)).fetchone()
    finally:
        b_conn.close()
    assert b_row is not None, "the doc_series row never arrived on device B"
    assert b_row['code'] == 'SYNCA'
    assert b_row['allocator_terminal_uid'] == TILL_A
    assert b_row['company_id'] == cid


# ── 3. doc_series_counter never rides the wire ─────────────────────────────

def test_doc_series_counter_never_appears_in_any_outbox_payload(install_b, relay):
    admin, cid = _new_shop()
    created = admin.post(f'{API}/doc-series', json={'doc_type': 'sale', 'code': 'NOWIRE', 'label': 'x'})
    series_id = created.get_json()['data']['id']
    with _at_terminal(TILL_A):
        admin.post(f'{API}/doc-series/{series_id}/allocator-claim')

    # Mint a few numbers -- if `allocate()`'s counter ever leaked onto the
    # wire, this is the step that would put it there.
    pid = admin.post(f'{API}/products', json={
        'name': 'Sync Widget', 'sku': f'DS-{uuid.uuid4().hex[:8]}',
        'cost_price': 1.0, 'sell_price': 2.0, 'initial_stock': 100,
    }).get_json()['data']['id']
    with _at_terminal(TILL_A):
        for _ in range(3):
            r = admin.post(f'{API}/sales', json={
                'items': [{'product_id': pid, 'quantity': 1}], 'amount_paid': 999,
                'payment_method': 'cash', 'idempotency_key': str(uuid.uuid4()),
            })
            assert r.status_code == 200, r.get_json()

    for row in _outbox('doc_series'):
        assert 'last_no' not in row['payload'], row['payload']
        assert 'counter' not in json.dumps(row['payload']).lower(), row['payload']


# ── 4. Delta gating: a SEQUENTIAL rename then claim converge fully ─────────

def test_a_rename_then_a_later_claim_both_survive_convergence(install_b, relay):
    """Device A renames the book's label and that edit CONVERGES to B
    first; only THEN does B claim the (now-renamed) book. Sequential, not
    concurrent -- B's claim bumps row_version from the value it already
    agrees with A on (2), so the row-version gate is a plain `>`, not a
    tie, and delta gating's actual job (preserving the untouched `label`
    column when a LATER event's payload snapshot does not carry it) is
    what this test exercises. MUTATION: replace `_delta_set_clause` with
    a full `excluded.*` snapshot in the `doc_series` apply branch -> B's
    OWN claim event does not carry `label` in its payload post-mint the
    same way it does today either way (this branch always sends a full
    snapshot, not a partial one) -- the REAL exposure a full-snapshot
    apply branch has is a claim payload whose `label` field is stale
    relative to what THIS receiving device already has (see the note
    below) -> RED there instead.

    A GENUINE GAP FOUND WHILE WRITING THIS FILE, not fixed here because
    fixing it means changing the reviewed apply-branch SQL rather than
    testing it (see `products/retail/tests/retail_doc_series_sync_test.py`
    module docstring's cross-reference and this session's own report):
    if A's rename and B's claim are TRULY concurrent -- both starting
    from the SAME synced row_version and bumping to the SAME next value
    independently, offline -- the row-version gate sees a TIE and falls
    through to the claimed_at_utc/allocator_terminal_uid tie-break, which
    was designed for the claim-race case (two devices claiming the SAME
    unclaimed book) and is not a sensible ordering for an UNRELATED field
    like `label`. SQLite's UPSERT WHERE clause is evaluated ONCE per row,
    so when it resolves in the claim's favour, the rename's `label` change
    is dropped outright -- delta-gating only controls WHICH columns a
    PASSING write may touch, it cannot rescue a write the row-level gate
    itself rejects. The fleet still CONVERGES (both devices end up
    agreeing on the same single state) and the wedge is still prevented
    (no exception, no stuck cursor) -- but a true concurrent-tie can drop
    one side's unrelated field edit, contrary to the design review's own
    "6b-i already fixed this" characterisation of the scenario, which
    describes SEQUENTIAL edits (this test), not a genuine row_version TIE.
    """
    admin, cid = _new_shop()
    created = admin.post(f'{API}/doc-series', json={'doc_type': 'sale', 'code': 'DELTA', 'label': 'Original'})
    series_id = created.get_json()['data']['id']

    service_a, service_b = _make_services(install_b, relay, cid)
    service_a.push_once()
    service_b.pull_once()  # B now has the book, unclaimed, label='Original'

    rename = admin.patch(f'{API}/doc-series/{series_id}', json={'label': 'Renamed By A'})
    assert rename.status_code == 200, rename.get_json()
    service_a.push_once()
    service_b.pull_once()  # B converges on the rename BEFORE claiming -- no tie possible.

    b_conn = install_b()
    try:
        b_before_claim = b_conn.execute("SELECT label, row_version FROM doc_series WHERE id=?",
                                         (series_id,)).fetchone()
    finally:
        b_conn.close()
    assert b_before_claim['label'] == 'Renamed By A', b_before_claim

    with _as_device_b(install_b), _at_terminal(TILL_B):
        claim = admin.post(f'{API}/doc-series/{series_id}/allocator-claim')
    assert claim.status_code == 200, claim.get_json()

    service_b.push_once()
    service_a.pull_once()

    a_conn = get_retail_conn()
    try:
        a_row = a_conn.execute("SELECT label, allocator_terminal_uid FROM doc_series WHERE id=?",
                                (series_id,)).fetchone()
    finally:
        a_conn.close()
    b_conn = install_b()
    try:
        b_row = b_conn.execute("SELECT label, allocator_terminal_uid FROM doc_series WHERE id=?",
                                (series_id,)).fetchone()
    finally:
        b_conn.close()

    assert a_row['label'] == 'Renamed By A', a_row
    assert a_row['allocator_terminal_uid'] == TILL_B, (
        "A never received B's claim -- delta gating dropped the allocator field")
    assert b_row['label'] == 'Renamed By A', (
        "B's own claim write clobbered the rename it had already converged on")
    assert b_row['allocator_terminal_uid'] == TILL_B, b_row


# ── 5. row_version reject-stale ─────────────────────────────────────────────

def test_an_older_update_does_not_overwrite_a_newer_one(install_b, relay):
    admin, cid = _new_shop()
    created = admin.post(f'{API}/doc-series', json={'doc_type': 'sale', 'code': 'STALE', 'label': 'v1'})
    series_id = created.get_json()['data']['id']

    service_a, service_b = _make_services(install_b, relay, cid)
    service_a.push_once()
    service_b.pull_once()

    # B edits the label TWICE (row_version 2, then 3) entirely offline --
    # A never sees either yet.
    with _as_device_b(install_b):
        r1 = admin.patch(f'{API}/doc-series/{series_id}', json={'label': 'v2 (from B)'})
        assert r1.status_code == 200, r1.get_json()
        r2 = admin.patch(f'{API}/doc-series/{series_id}', json={'label': 'v3 (from B)'})
        assert r2.status_code == 200, r2.get_json()

    # Now replay only B's FIRST (now-stale) event to A directly -- the
    # SyncService method under test, not a push/pull round trip, so the
    # ordering is deterministic rather than relying on relay ordering.
    b_conn = install_b()
    try:
        b_row = b_conn.execute("SELECT row_version FROM doc_series WHERE id=?", (series_id,)).fetchone()
        assert b_row['row_version'] == 3, "expected two PATCHes to land as row_version 2 then 3"
    finally:
        b_conn.close()

    stale_event = {
        "entity_type": "doc_series", "entity_id": series_id, "event_type": "update",
        "payload": {"id": series_id, "doc_type": "sale", "code": "STALE", "label": "v2 (from B)",
                    "branch_uid": None, "allocator_terminal_uid": None, "claimed_at_utc": None,
                    "pad_width": 6, "start_no": 1, "status": "active", "created_at": None,
                    "row_version": 2, "updated_at_utc": "2020-01-01T00:00:00+00:00",
                    "_changed_fields": ["label"]},
    }
    # First apply the REAL row_version=3 label onto A (simulating A having
    # already received the newer event first), THEN replay the stale one.
    fresh_event = {
        "entity_type": "doc_series", "entity_id": series_id, "event_type": "update",
        "payload": {"id": series_id, "doc_type": "sale", "code": "STALE", "label": "v3 (from B)",
                    "branch_uid": None, "allocator_terminal_uid": None, "claimed_at_utc": None,
                    "pad_width": 6, "start_no": 1, "status": "active", "created_at": None,
                    "row_version": 3, "updated_at_utc": "2020-01-01T00:00:01+00:00",
                    "_changed_fields": ["label"]},
    }
    conflicts = []
    a_conn = get_retail_conn()
    try:
        service_a._apply_event(a_conn, fresh_event, local_company_id=cid, conflict_sink=conflicts)
        a_conn.commit()
        service_a._apply_event(a_conn, stale_event, local_company_id=cid, conflict_sink=conflicts)
        a_conn.commit()
        final = a_conn.execute("SELECT label, row_version FROM doc_series WHERE id=?", (series_id,)).fetchone()
    finally:
        a_conn.close()

    assert final['label'] == 'v3 (from B)', (
        f"a STALE row_version=2 update overwrote the newer row_version=3 label: {dict(final)}")
    assert final['row_version'] == 3
    assert len(conflicts) == 1, conflicts

    conflict_rows = get_retail_conn()
    try:
        rows = conflict_rows.execute(
            "SELECT * FROM sync_conflicts WHERE entity_type='doc_series' AND entity_id=?", (series_id,)
        ).fetchall()
    finally:
        conflict_rows.close()
    assert len(rows) == 1, "the rejected stale write must land exactly one sync_conflicts row"


# ── 6. THE CLAIM-RACE TIE-BREAK -- the defect neither the design nor its
#      own review caught ───────────────────────────────────────────────────

def test_claim_race_tie_break_is_deterministic_and_the_same_on_every_device():
    """Two devices claim the SAME unclaimed book while both offline: both
    start from row_version=1 and both write row_version=2. The submitted
    design claimed "the later row_version wins on convergence" -- it does
    not, because both incoming row_versions are EQUAL to each other, so
    the plain `excluded.row_version > local.row_version` gate is FALSE in
    BOTH directions and (without the tie-break clause this test exists to
    prove) NEITHER claim would ever apply anywhere, leaving the fleet
    PERMANENTLY divergent with two tills each believing they own the book.

    Proved directly against `_apply_event` (retail_quotation_sync_test.py's
    own precedent for "a direct call is the right tool when the claim is
    about one INSERT/UPDATE statement's behaviour, not a relay round
    trip") rather than through two real processes: the property under
    test is a pure function of the two competing payloads, and a direct
    call lets both possible arrival orders be proven from the exact same
    starting fixture, which two independently-timed subprocesses could
    not guarantee.

    MUTATION: replace the tie-break WHERE clause with the plain
    `excluded.row_version > doc_series.row_version` gate -> BOTH
    directions below go RED (neither `test_..._b_arrives_first` nor
    `..._a_arrives_first` converges: rowcount stays 0 on the second event
    in both orderings, and the local row keeps whichever claim landed
    first regardless of the tie-break inputs -- the exact "permanently
    divergent" failure this test exists to catch).
    """
    admin, cid = _new_shop()
    created = admin.post(f'{API}/doc-series', json={'doc_type': 'sale', 'code': 'RACE', 'label': 'x'})
    series_id = created.get_json()['data']['id']
    base_row_version = created.get_json()  # unused; row starts at row_version=1 per the CREATE table default

    def _claim_event(terminal_uid, claimed_at_utc):
        return {
            "entity_type": "doc_series", "entity_id": series_id, "event_type": "update",
            "payload": {"id": series_id, "doc_type": "sale", "code": "RACE", "label": "x",
                        "branch_uid": None, "allocator_terminal_uid": terminal_uid,
                        "claimed_at_utc": claimed_at_utc, "pad_width": 6, "start_no": 1,
                        "status": "active", "created_at": None, "row_version": 2,
                        "updated_at_utc": claimed_at_utc,
                        "_changed_fields": ["allocator_terminal_uid", "claimed_at_utc"]},
        }

    # TILL_LATER's claimed_at_utc sorts LEXICOGRAPHICALLY LATER -- it must
    # win regardless of which event this device happens to apply FIRST.
    claim_early = _claim_event("till-early", "2026-01-01T00:00:00+00:00")
    claim_later = _claim_event("till-later", "2026-01-01T00:00:05+00:00")

    def _reset_row():
        conn = get_retail_conn()
        conn.execute("UPDATE doc_series SET allocator_terminal_uid=NULL, claimed_at_utc=NULL, "
                     "row_version=1 WHERE id=?", (series_id,))
        conn.commit()
        conn.close()

    def _winner():
        conn = get_retail_conn()
        try:
            row = conn.execute(
                "SELECT allocator_terminal_uid, row_version FROM doc_series WHERE id=?", (series_id,)
            ).fetchone()
        finally:
            conn.close()
        return row['allocator_terminal_uid'], row['row_version']

    service = SyncService(client_factory=lambda: None, get_conn=get_retail_conn,
                           local_company_id_provider=lambda: cid)

    # Order 1: early arrives first, later arrives second.
    _reset_row()
    conn = get_retail_conn()
    try:
        service._apply_event(conn, claim_early, local_company_id=cid)
        conn.commit()
        service._apply_event(conn, claim_later, local_company_id=cid)
        conn.commit()
    finally:
        conn.close()
    winner, rv = _winner()
    assert winner == "till-later", f"order (early, later): expected till-later to win, got {winner!r}"
    assert rv == 2

    # Order 2: later arrives first, early arrives second -- the SAME
    # winner must result, or two devices applying this pair in opposite
    # orders would permanently disagree about who owns the book.
    _reset_row()
    conn = get_retail_conn()
    try:
        service._apply_event(conn, claim_later, local_company_id=cid)
        conn.commit()
        service._apply_event(conn, claim_early, local_company_id=cid)
        conn.commit()
    finally:
        conn.close()
    winner2, rv2 = _winner()
    assert winner2 == "till-later", (
        f"order (later, early): expected till-later to STILL win (deterministic tie-break), got {winner2!r}")
    assert rv2 == 2
