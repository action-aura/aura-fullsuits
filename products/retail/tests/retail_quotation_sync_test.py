"""Aura Retail -- Aseel-parity wave A-PAR, "quotations and sales orders as
real documents" (schema v33): TWO-DEVICE CONVERGENCE (Stage B).

Proves the `quotation` entity type's `_apply_event` branch (commercial_
runtime/sync/sync_service.py) and its emit side (retail_api.py's quotation
routes): branch_uid resolution (never the sender's raw branch_id -- AUDIT-
032C), missing-parent quarantine for a line's product_id (the sale_item
branch's own vocabulary, reused rather than reinvented), DRAFTS DO NOT SYNC,
an `update` event never touching lines, every writer bumping row_version,
and the cross-device double-conversion conflict being recorded rather than
silently overwriting the local link.

HARNESS: adapted from retail_cheque_sync_test.py's own in-process two-install
technique (`install_b` + `InMemoryRelay` + real `SyncService`) for the tests
that need genuine two-device convergence (a, b), and from retail_stock_sync_
apply_hardening_test.py's direct `SyncService._apply_event(...)` calls for
the tests that are really about ONE INSERT/UPDATE statement's own behaviour
(d, f) -- the right tool for "does this apply branch do what it claims"
rather than a full relay round trip. Tests c and e need no second device at
all: they are about the EMIT side (retail_api.py's own sync_outbox writes),
proven directly against a real Flask app's own database.

Run (one file per process, AUDIT-010):
    pytest products/retail/tests/retail_quotation_sync_test.py -v
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_quotationsync_"))
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


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


def _new_shop():
    company_id = str(uuid.uuid4())
    email = f'qtsync-admin-{uuid.uuid4().hex[:8]}@test.local'
    password = 'QuotationSyncPW1'
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


def _create_product(admin, sell_price=20.0, initial_stock=1000):
    r = admin.post(f'{API}/products', json={
        'name': f'QT Sync Widget {uuid.uuid4().hex[:6]}', 'sku': f'QTS-{uuid.uuid4().hex[:8]}',
        'cost_price': sell_price / 2, 'sell_price': sell_price, 'tax_rate': 0, 'initial_stock': initial_stock,
    })
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


class InMemoryRelay:
    """Identical to retail_cheque_sync_test.py's own double."""

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
    """Identical technique to retail_cheque_sync_test.py's own fixture."""
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
    """See retail_cheque_sync_test.py's identical fixture for the full
    reasoning -- install A is booted once at module import time and shared
    by every test in this file."""
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
    """Runs the enclosed real HTTP calls (via a Flask test client already
    logged in through the SHARED registry database) against device B's OWN
    separate retail.db instead of device A's -- the identical adaptation
    retail_cheque_sync_test.py's `_bounce_on_device_b` makes to drive real
    route logic against a second physical database in one process, applied
    here at the `get_retail_conn` accessor itself (create_sale/the
    quotation routes have no conn-parameterized inner function to call
    directly, unlike `_apply_cheque_event`)."""
    with mock.patch.object(retail_api_module, 'get_retail_conn', side_effect=get_conn):
        yield


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


# ── a. A pulled quotation lands on THIS device's own branch ────────────────

def test_pulled_quotation_lands_on_this_devices_own_branch(install_b, relay):
    """Device B's own "Main Branch" sits at a DIFFERENT local id than
    device A's quotation branch. Pull A's quotation; assert `sales_
    quotations.branch_id` equals B's OWN local id for that `branch_uid`,
    and assert GET /quotations/committed-demand?branch_id=<B's id> counts
    it once accepted.
    MUTATION: write `p.get("branch_id")` raw (the `reorder_request`
    spelling, AUDIT-032C) instead of resolving `branch_uid` through
    `_resolve_branch_id` -> RED on both halves -- B's row would land under
    whatever integer A's OWN branch happened to be, which usually means a
    branch B does not even have.
    """
    # B already has TWO branches of its own before it ever pulls anything,
    # so the branch this test cares about does NOT coincidentally land at
    # the same local id on both devices -- a passing test that used
    # matching ids on both sides would prove nothing about resolution.
    b_conn = install_b()
    for _ in range(2):
        b_conn.execute(
            "INSERT INTO branches (company_id,name,address,phone,uid) VALUES (?,?,?,?,?)",
            ('unrelated-company', f'B Local Branch {uuid.uuid4().hex[:4]}', '', '', str(uuid.uuid4())))
    b_conn.commit()
    b_conn.close()

    admin, cid = _new_shop()
    pid = _create_product(admin)
    # Product creation self-heals company cid's OWN default branch (id=1,
    # NEVER synced -- _default_branch's own docstring) -- the quotation
    # below is explicitly filed under a SECOND, EXPLICITLY created branch,
    # which DOES sync (branch is a Phase-B-wave synced entity type).
    br = admin.post(f'{API}/branches', json={'name': 'A Explicit Branch', 'address': '', 'phone': ''})
    assert br.status_code == 200, br.get_json()
    a_branch_id = br.get_json()['data']['id']

    r = admin.post(f'{API}/quotations', json={'branch_id': a_branch_id,
                                               'items': [{'product_id': pid, 'quantity': 3}]})
    assert r.status_code == 201, r.get_json()
    qid = r.get_json()['data']['id']
    assert admin.post(f'{API}/quotations/{qid}/send').status_code == 200
    assert admin.post(f'{API}/quotations/{qid}/accept').status_code == 200

    service_a = SyncService(client_factory=lambda: relay, get_conn=get_retail_conn)
    service_b = SyncService(client_factory=lambda: relay, get_conn=install_b,
                             local_company_id_provider=lambda: cid)
    service_a.push_once()
    service_b.pull_once()

    b_conn = install_b()
    try:
        b_branch = b_conn.execute(
            "SELECT id FROM branches WHERE uid=(SELECT uid FROM branches WHERE company_id=? AND name=?)",
            (cid, 'A Explicit Branch')).fetchone()
        assert b_branch is not None, 'the branch itself never arrived on B'
        b_branch_id = b_branch['id']
        assert b_branch_id != a_branch_id, (
            'this fixture is only meaningful if the two devices disagree on the local id -- '
            f'both landed on {a_branch_id}')

        q_row = b_conn.execute("SELECT branch_id FROM sales_quotations WHERE id=?", (qid,)).fetchone()
        assert q_row is not None, 'the quotation itself never arrived on B'
        assert q_row['branch_id'] == b_branch_id, (
            f"B's quotation.branch_id ({q_row['branch_id']}) must equal B's OWN local branch id "
            f"({b_branch_id}), not A's ({a_branch_id})")
    finally:
        b_conn.close()

    with _as_device_b(install_b):
        demand = admin.get(f'{API}/quotations/committed-demand?branch_id={b_branch_id}')
    assert demand.status_code == 200, demand.get_json()
    rows = {row['product_id']: row['committed'] for row in demand.get_json()['data']}
    assert rows.get(pid) == 3, rows


# ── b. A quotation whose product has not arrived is parked, not fatal ─────

def test_quotation_whose_products_have_not_arrived_are_parked_not_fatal(install_b, relay):
    """Relay a quotation without its own product's create event ever having
    reached B; assert a `sync_apply_quarantine` row with reason
    'missing_parent:product', assert the cursor ADVANCED past it, and
    assert a LATER, unrelated quotation in the SAME batch still applied.
    MUTATION: remove the `_row_exists` check from the `quotation` apply
    branch -> `sqlite3.IntegrityError: FOREIGN KEY constraint failed`,
    cursor stuck at 0, nothing in the batch applies -- not even the later,
    unrelated quotation. That third assertion is what distinguishes
    "parked" from "the whole batch died quietly".
    """
    admin, cid = _new_shop()
    pid_missing = _create_product(admin)
    r1 = admin.post(f'{API}/quotations', json={'items': [{'product_id': pid_missing, 'quantity': 1}]})
    qid_missing = r1.get_json()['data']['id']
    admin.post(f'{API}/quotations/{qid_missing}/send')

    pid_ok = _create_product(admin)
    r2 = admin.post(f'{API}/quotations', json={'items': [{'product_id': pid_ok, 'quantity': 1}]})
    qid_ok = r2.get_json()['data']['id']
    admin.post(f'{API}/quotations/{qid_ok}/send')

    service_a = SyncService(client_factory=lambda: relay, get_conn=get_retail_conn)
    service_b = SyncService(client_factory=lambda: relay, get_conn=install_b,
                             local_company_id_provider=lambda: cid)
    service_a.push_once()

    # Strip ONLY pid_missing's own product-create event from the relay --
    # simulating Owner's push-side quarantine skipping one product while
    # relaying everything else, exactly the shape sync_service.py's own
    # sale_item-branch comment describes for the identical hazard.
    relay.events = [
        ev for ev in relay.events
        if not (ev['entity_type'] == 'product' and ev['entity_id'] == pid_missing)
    ]

    cursor_before = install_b().execute("SELECT last_seq FROM sync_cursor WHERE id=1").fetchone()[0]
    service_b.pull_once()
    cursor_after = install_b().execute("SELECT last_seq FROM sync_cursor WHERE id=1").fetchone()[0]
    assert cursor_after > cursor_before, (
        'the cursor must advance PAST the quarantined event, not stall on it -- a parked event '
        'is retried on the next tick, never blocks the batch it arrived in')

    b_conn = install_b()
    try:
        quarantine = b_conn.execute(
            "SELECT * FROM sync_apply_quarantine WHERE entity_id=?", (qid_missing,)).fetchall()
        assert len(quarantine) == 1, quarantine
        assert quarantine[0]['reason'] == 'missing_parent:product', dict(quarantine[0])

        missing_q = b_conn.execute("SELECT * FROM sales_quotations WHERE id=?", (qid_missing,)).fetchone()
        assert missing_q is None, 'a parked quotation must not have a header row at all'

        ok_q = b_conn.execute("SELECT * FROM sales_quotations WHERE id=?", (qid_ok,)).fetchone()
        assert ok_q is not None, (
            'the LATER, unrelated quotation in the same batch must still have applied -- '
            'a quarantine must not silently kill the rest of the batch')
        ok_lines = b_conn.execute(
            "SELECT COUNT(*) FROM sales_quotation_lines WHERE quotation_id=?", (qid_ok,)).fetchone()[0]
        assert ok_lines == 1
    finally:
        b_conn.close()


# ── c. A draft emits no sync event; send emits one carrying its lines ─────

def test_a_draft_emits_no_sync_event_and_send_emits_one_carrying_its_lines():
    """Create a draft: assert ZERO sync_outbox rows for entity_type
    'quotation'. Send it: assert exactly one, event_type='create', with
    payload['lines'] complete and payload['branch_uid'] non-null.
    MUTATION A: emit on create -> the draft half goes red (nonzero rows
    before send).
    MUTATION B: drop 'lines' from the send payload -> a peer's line count
    would read 0 -- caught here directly rather than only transitively via
    test (a)/(b)'s own two-device assertions.
    """
    admin, cid = _new_shop()
    pid = _create_product(admin)
    r = admin.post(f'{API}/quotations', json={'items': [{'product_id': pid, 'quantity': 2, 'discount_pct': 5}]})
    assert r.status_code == 201, r.get_json()
    qid = r.get_json()['data']['id']

    assert _outbox('quotation') == [], 'a DRAFT must never queue a sync event'

    s = admin.post(f'{API}/quotations/{qid}/send')
    assert s.status_code == 200, s.get_json()

    events = _outbox('quotation')
    assert len(events) == 1, events
    ev = events[0]
    assert ev['event_type'] == 'create'
    assert ev['payload']['branch_uid'], 'branch_uid must be non-null -- never the raw branch_id integer'
    lines = ev['payload'].get('lines')
    assert lines and len(lines) == 1, ev['payload']
    assert lines[0]['product_id'] == pid
    assert lines[0]['quantity'] == 2
    assert lines[0]['discount_pct'] == 5


# ── d. An update event never replaces lines ─────────────────────────────────

def test_an_update_event_never_replaces_lines(install_b):
    """Apply a 'create' event carrying two lines, then a hand-built
    'update' event for the SAME quotation carrying DIFFERENT lines; assert
    the local lines are byte-for-byte unchanged.
    MUTATION: make the apply branch also insert lines on an 'update' event
    -> RED. Proves the immutability the design RELIES on (sent is
    immutable, so there is no line-replace event on the wire) rather than
    merely trusting it.
    """
    b_conn = install_b()
    try:
        company_id = 'company-d'
        b_conn.execute(
            "INSERT INTO branches (company_id,name,address,phone,uid) VALUES (?,?,?,?,?)",
            (company_id, 'Main', '', '', str(uuid.uuid4())))
        b_conn.commit()
        branch_uid = b_conn.execute("SELECT uid FROM branches WHERE company_id=?", (company_id,)).fetchone()['uid']

        product_a = str(uuid.uuid4())
        product_b = str(uuid.uuid4())
        for pid, name in ((product_a, 'Product A'), (product_b, 'Product B')):
            b_conn.execute(
                "INSERT INTO products (id, company_id, sku, barcode, name, cost_price, sell_price, tax_rate, "
                "unit, reorder_level, status) VALUES (?,?,?,?,?,5,10,0,'pcs',5,'active')",
                (pid, company_id, f'SKU-{pid[:8]}', '', name))
        b_conn.commit()

        service_b = SyncService(client_factory=lambda: None, get_conn=install_b,
                                 local_company_id_provider=lambda: company_id)
        qid = str(uuid.uuid4())
        line_id = str(uuid.uuid4())
        create_ev = {
            "entity_type": "quotation", "event_type": "create",
            "payload": {
                "id": qid, "branch_id": 999, "branch_uid": branch_uid,
                "customer_id": None, "doc_number": f"QUO-{qid[:8]}", "doc_kind": "quotation",
                "status": "sent", "valid_until": None, "currency": "JOD", "tax_mode": "after_discount",
                "subtotal": 10, "discount_amount": 0, "tax_amount": 0, "total": 10, "notes": "",
                "created_at": "2026-09-15 10:00:00", "created_by": "System",
                "sent_at": "2026-09-15 10:00:00", "accepted_at": None, "declined_at": None,
                "cancelled_at": None, "converted_at": None, "converted_sale_uid": None,
                "conversion_variance_json": None, "row_version": 2, "updated_at_utc": "2026-09-15T10:00:00+00:00",
                "lines": [{
                    "id": line_id, "product_id": product_a, "product_name_snapshot": "Product A",
                    "quantity": 1, "unit_price": 10, "discount_pct": 0, "tax_rate": 0, "line_total": 10,
                    "promotion_name_snapshot": None, "line_no": 1,
                }],
            },
        }
        service_b._apply_event(b_conn, create_ev, local_company_id=company_id)
        b_conn.commit()

        before = [dict(r) for r in b_conn.execute(
            "SELECT * FROM sales_quotation_lines WHERE quotation_id=? ORDER BY line_no", (qid,)).fetchall()]
        assert len(before) == 1
        assert before[0]['product_id'] == product_a

        update_ev = {
            "entity_type": "quotation", "event_type": "update",
            "payload": dict(create_ev['payload'], status='accepted', accepted_at='2026-09-15 11:00:00',
                            row_version=3, updated_at_utc='2026-09-15T11:00:00+00:00',
                            _changed_fields=['status', 'accepted_at', 'row_version', 'updated_at_utc'],
                            # A future bug/corrupt payload: an update carrying a
                            # WHOLLY DIFFERENT line set for the same quotation id.
                            lines=[{
                                "id": str(uuid.uuid4()), "product_id": product_b,
                                "product_name_snapshot": "Product B", "quantity": 99,
                                "unit_price": 10, "discount_pct": 0, "tax_rate": 0, "line_total": 990,
                                "promotion_name_snapshot": None, "line_no": 1,
                            }]),
        }
        service_b._apply_event(b_conn, update_ev, local_company_id=company_id)
        b_conn.commit()

        after = [dict(r) for r in b_conn.execute(
            "SELECT * FROM sales_quotation_lines WHERE quotation_id=? ORDER BY line_no", (qid,)).fetchall()]
        assert after == before, (
            f"an 'update' event must NEVER touch lines -- got {after}, expected unchanged {before}")
        q = b_conn.execute("SELECT status FROM sales_quotations WHERE id=?", (qid,)).fetchone()
        assert q['status'] == 'accepted', 'the header itself must still have applied the update'
    finally:
        b_conn.close()


# ── e. row_version is bumped by every writer ────────────────────────────────

def test_row_version_is_bumped_by_every_writer():
    """Send, accept, cancel a quotation and assert row_version strictly
    increases each time.
    MUTATION: drop one bump -> RED. v17's own lesson (database/schema.py):
    reject-stale on a column nothing bumps silently discards every write
    from every device on day one.
    """
    admin, cid = _new_shop()
    pid = _create_product(admin)
    r = admin.post(f'{API}/quotations', json={'items': [{'product_id': pid, 'quantity': 1}]})
    qid = r.get_json()['data']['id']

    conn = get_retail_conn()
    v0 = conn.execute("SELECT row_version FROM sales_quotations WHERE id=?", (qid,)).fetchone()['row_version']
    conn.close()

    admin.post(f'{API}/quotations/{qid}/send')
    conn = get_retail_conn()
    v1 = conn.execute("SELECT row_version FROM sales_quotations WHERE id=?", (qid,)).fetchone()['row_version']
    conn.close()
    assert v1 > v0, (v0, v1)

    admin.post(f'{API}/quotations/{qid}/accept')
    conn = get_retail_conn()
    v2 = conn.execute("SELECT row_version FROM sales_quotations WHERE id=?", (qid,)).fetchone()['row_version']
    conn.close()
    assert v2 > v1, (v1, v2)

    admin.post(f'{API}/quotations/{qid}/cancel')
    conn = get_retail_conn()
    v3 = conn.execute("SELECT row_version FROM sales_quotations WHERE id=?", (qid,)).fetchone()['row_version']
    conn.close()
    assert v3 > v2, (v2, v3)


# ── f. A conflicting conversion is recorded, not overwritten ───────────────

def test_conflicting_conversion_is_recorded_not_overwritten(install_b, relay):
    """Local row (device B) already converted to sale X; apply an incoming
    event converting it to sale Y. Assert `converted_sale_uid` is STILL X,
    and a `sync_conflicts` row exists naming both.
    MUTATION: let the delta overwrite `converted_sale_uid` unconditionally
    -> RED; the local sale loses its link with nothing anywhere reporting
    it (C5's real exposure -- the genuine cross-device double-conversion
    case, not the single-database race tests 10/11 in retail_quotation_
    test.py cover).

    The incoming event's row_version is DELIBERATELY set HIGHER than B's
    own local value -- otherwise the ORDINARY reject-stale gate alone would
    already refuse the whole write (both devices converging from the same
    base and bumping once each land on the SAME row_version, which the
    reject-stale gate ALSO blocks, generically) and this test would prove
    nothing about the double-conversion-SPECIFIC code path at all. MEASURED
    directly: with an equal incoming row_version, this test stays green
    even if the double-conversion branch is deleted outright, because the
    generic `WHERE ... > row_version` gate already protects the row for an
    unrelated reason.
    """
    admin, cid = _new_shop()
    pid = _create_product(admin)
    r = admin.post(f'{API}/quotations', json={'items': [{'product_id': pid, 'quantity': 1}]})
    qid = r.get_json()['data']['id']
    admin.post(f'{API}/quotations/{qid}/send')
    admin.post(f'{API}/quotations/{qid}/accept')

    service_a = SyncService(client_factory=lambda: relay, get_conn=get_retail_conn)
    service_b = SyncService(client_factory=lambda: relay, get_conn=install_b,
                             local_company_id_provider=lambda: cid)
    service_a.push_once()
    service_b.pull_once()

    b_conn = install_b()
    try:
        b_row = dict(b_conn.execute("SELECT * FROM sales_quotations WHERE id=?", (qid,)).fetchone())
        assert b_row['status'] == 'accepted', b_row
        local_sale_uid = 'LOCAL-DEVICE-B-SALE-UID'
        b_conn.execute(
            "UPDATE sales_quotations SET status='converted', converted_sale_uid=?, row_version=row_version+1 "
            "WHERE id=?", (local_sale_uid, qid))
        b_conn.commit()
        b_row_version_after_local_convert = b_conn.execute(
            "SELECT row_version FROM sales_quotations WHERE id=?", (qid,)).fetchone()['row_version']

        incoming_sale_uid = 'INCOMING-DEVICE-A-SALE-UID'
        incoming_payload = dict(b_row)
        incoming_payload['status'] = 'converted'
        incoming_payload['converted_sale_uid'] = incoming_sale_uid
        incoming_payload['converted_at'] = '2026-09-15 12:00:00'
        incoming_payload['conversion_variance_json'] = json.dumps({'mode': 'live'})
        # STRICTLY GREATER than B's own post-local-convert row_version -- see
        # this test's own docstring for why equality would prove nothing.
        incoming_payload['row_version'] = b_row_version_after_local_convert + 1
        incoming_payload['branch_uid'] = b_conn.execute(
            "SELECT uid FROM branches WHERE id=?", (b_row['branch_id'],)).fetchone()['uid']
        incoming_payload['_changed_fields'] = [
            'status', 'converted_at', 'converted_sale_uid', 'conversion_variance_json', 'row_version']

        ev = {"entity_type": "quotation", "event_type": "update", "payload": incoming_payload}
        service_b._apply_event(b_conn, ev, local_company_id=cid, conflict_sink=[])
        b_conn.commit()

        after = dict(b_conn.execute("SELECT * FROM sales_quotations WHERE id=?", (qid,)).fetchone())
        assert after['converted_sale_uid'] == local_sale_uid, (
            f"device B's own conversion link must survive -- got {after['converted_sale_uid']!r}, "
            f"expected the LOCAL uid {local_sale_uid!r} to survive an incoming conflicting event")

        conflicts = b_conn.execute(
            "SELECT * FROM sync_conflicts WHERE entity_type='quotation' AND entity_id=?", (qid,)).fetchall()
        assert len(conflicts) == 1, conflicts
        conflict = dict(conflicts[0])
        assert conflict['event_type'] == 'double_conversion', conflict
        incoming_recorded = json.loads(conflict['incoming_payload'])
        assert incoming_recorded['converted_sale_uid'] == incoming_sale_uid, (
            'the conflict row must name the INCOMING sale_uid that lost, for the shop to actually '
            'investigate the collision')
    finally:
        b_conn.close()
