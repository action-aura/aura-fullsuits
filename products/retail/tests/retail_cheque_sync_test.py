"""Aura Retail -- Aseel-parity wave A-PAR, "cheque lifecycle as a tracked
instrument" (schema v32): TWO-DEVICE CONVERGENCE.

This is the file that proves B2 in the design review this implements: the
submitted design's mutable `status` column + row_version gate looked correct
on ONE device and DOUBLE-APPLIED the money leg the moment two devices each
observed the same bounce. core/retail/cheques.py's own unit tests
(retail_cheque_fold_test.py) prove the FOLD converges for every arrival-order
permutation of a hand-built event set; THIS file proves the whole stack --
real routes, real sync_service.py apply branches, a real relay round trip --
converges too, which is the level a fold-only proof cannot reach on its own.

HARNESS: adapted from retail_money_sync_test.py's own in-process two-install
technique (`install_b` + `InMemoryRelay` + real `SyncService`), NOT the
subprocess-based `_accept_device.py` family -- that harness's device script
has no cheque ops and this file does not need genuine process isolation to
prove convergence, only two independent SQLite databases and one relay.

THE ONE ADAPTATION THIS FILE MAKES TO THAT PATTERN: retail_money_sync_test.py
never has "device B" WRITE a business event of its own -- it only ever
RECEIVES what A pushes, because `install_b` is a bare temp database with no
Flask app in front of it. Test 10 below needs device B to independently
BOUNCE the same cheque, which means calling the real `_apply_cheque_event`
route logic against B's own connection. Flask's `app.test_request_context()`
supplies the session context that function's own `_uid()`/`_cid()` calls
need, without spinning up a second real HTTP server or process -- the
route's OWN code runs unmodified against B's OWN database, which is exactly
the fidelity this proof needs and the fold-only unit test cannot provide.

Run:
    pytest products/retail/tests/retail_cheque_sync_test.py -v
"""
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_chequesync_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402
seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")

import app as _app_module  # noqa: E402

app = _app_module.init_app()  # install A: a real, fully-booted Flask app
app.config["TESTING"] = True

from api import retail_api  # noqa: E402
from api.retail_api import _ensure_credit_schema  # noqa: E402
from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402
from commercial_runtime.sync.sync_service import RETAIL_SYNC_ENTITY_TYPES, SyncService  # noqa: E402
import database.schema as schema  # noqa: E402
from database.schema import get_retail_conn  # noqa: E402
from core.retail import cheques as cheque_engine  # noqa: E402

API = '/api/sub/retail'


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


def _new_shop(price=100.0):
    company_id = str(uuid.uuid4())
    email = f'chqsync-admin-{uuid.uuid4().hex[:8]}@test.local'
    password = 'ChqSyncPW1'
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
    p = admin.post(f'{API}/products', json={
        'name': 'Chq Sync Widget', 'sku': f'CQS-{uuid.uuid4().hex[:8]}',
        'cost_price': price / 2, 'sell_price': price, 'tax_rate': 0, 'initial_stock': 5000})
    assert p.status_code == 200, p.get_json()
    return admin, company_id, p.get_json()['data']['id']


def _make_credit_customer(admin, amount, product_id):
    r = admin.post(f'{API}/customers', json={'name': f'Chq Sync Customer {uuid.uuid4().hex[:6]}'})
    assert r.status_code == 200, r.get_json()
    cust_id = r.get_json()['data']['id']
    conn = get_retail_conn()
    conn.execute("UPDATE customers SET credit_mode='unlimited' WHERE id=?", (cust_id,))
    conn.commit()
    conn.close()
    sale = admin.post(f'{API}/sales', json={
        'items': [{'product_id': product_id, 'quantity': 1}],
        'customer_id': cust_id, 'payment_method': 'credit', 'amount_paid': 0,
        'idempotency_key': str(uuid.uuid4()),
    })
    assert sale.status_code == 200, sale.get_json()
    return cust_id


class InMemoryRelay:
    """Identical to retail_money_sync_test.py's own double."""

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
    """Identical technique to retail_money_sync_test.py's own fixture --
    see that file's module docstring for the full reasoning."""
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

    import api.retail_api as _retail_api_module
    _previous_flag = _retail_api_module._CREDIT_SCHEMA_READY
    _retail_api_module._CREDIT_SCHEMA_READY = False
    try:
        _ensure_credit_schema(_get_conn())
    finally:
        _retail_api_module._CREDIT_SCHEMA_READY = _previous_flag

    return _get_conn


@pytest.fixture(autouse=True)
def _clean_install_a_outbox():
    """See retail_money_sync_test.py's identical fixture for the full
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


def _b_folded_status(get_conn, cheque_id):
    conn = get_conn()
    try:
        events = conn.execute(
            "SELECT id, from_status, to_status, created_at_utc FROM cheque_events "
            "WHERE cheque_id=?", (cheque_id,)).fetchall()
        return cheque_engine.fold(events)
    finally:
        conn.close()


def _b_active_cheque_payments(get_conn, party_id):
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM payments WHERE party_id=? AND related_type='cheque' "
            "AND COALESCE(status,'active')='active' ORDER BY id", (party_id,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _bounce_on_device_b(get_conn, company_id, user_id, cheque_id, reason):
    """Calls the REAL route logic (`_apply_cheque_event`) against device B's
    OWN connection, inside a Flask test_request_context so `_uid()`/`_cid()`
    resolve -- see this module's own docstring for why this is the one
    adaptation to retail_money_sync_test.py's established pattern."""
    with app.test_request_context():
        from flask import session
        session['mt_user_id'] = user_id
        session['company_id'] = company_id
        conn = get_conn()
        try:
            cheque = conn.execute("SELECT * FROM cheques WHERE id=?", (cheque_id,)).fetchone()
            assert cheque is not None, 'device B must already hold this cheque header before bouncing it'
            status_code, body = retail_api._apply_cheque_event(
                conn, company_id, cheque, 'bounced', reason=reason)
            assert status_code == 200, body
            conn.commit()
        finally:
            conn.close()
    return body


# ── 9. Single-device-originated bounce converges to a second device ────────

def test_cheque_state_converges_to_a_second_device(install_b, relay):
    """Device A creates and bounces; device B ends with the same folded
    status, two cheque_events rows and BOTH payments rows.
    MUTATION A: remove 'cheque' from RETAIL_SYNC_ENTITY_TYPES -> RED.
    MUTATION B: omit 'cheque_event' from _ENTITY_TYPES_WITHOUT_COMPANY_ID ->
    a batch of only cheque_events raises on a SyncService with no
    local_company_id_provider -> RED.
    """
    admin, cid, pid = _new_shop(price=150.0)
    cust_id = _make_credit_customer(admin, 150.0, pid)
    create_r = admin.post(f'{API}/cheques', json={
        'direction': 'in', 'party_id': cust_id, 'amount': 150.0,
        'cheque_number': f'CHQ-{uuid.uuid4().hex[:8]}', 'due_date': '2026-12-01',
    })
    assert create_r.status_code == 201, create_r.get_json()
    cheque_id = create_r.get_json()['data']['id']

    bounce_r = admin.post(f'{API}/cheques/{cheque_id}/bounce', json={'reason': 'Insufficient funds'})
    assert bounce_r.status_code == 200, bounce_r.get_json()

    service_a = SyncService(client_factory=lambda: relay, get_conn=get_retail_conn)
    # SAME company id on B as A -- two TILLS of one shop, not two companies
    # (unlike retail_money_sync_test.py's own sale tests, which deliberately
    # rebind a sale to a DIFFERENT company on B; a cheque's party_id is a
    # customer id that only means anything within the SAME company).
    service_b = SyncService(client_factory=lambda: relay, get_conn=install_b,
                            local_company_id_provider=lambda: cid)
    service_a.push_once()
    service_b.pull_once()

    b_conn = install_b()
    try:
        b_cheque = b_conn.execute("SELECT * FROM cheques WHERE id=?", (cheque_id,)).fetchone()
        b_event_count = b_conn.execute(
            "SELECT COUNT(*) FROM cheque_events WHERE cheque_id=?", (cheque_id,)).fetchone()[0]
    finally:
        b_conn.close()
    assert b_cheque is not None
    assert b_cheque['company_id'] == cid
    assert b_event_count == 2  # created + bounced

    b_result = _b_folded_status(install_b, cheque_id)
    assert b_result.status == 'bounced'
    assert len(b_result.crossings) == 2

    b_payments = _b_active_cheque_payments(install_b, cust_id)
    assert len(b_payments) == 2, b_payments
    assert [p['direction'] for p in b_payments] == ['in', 'out']


# ── 10. Two devices bouncing the SAME cheque reverse the money ONCE ─────────

def test_two_devices_bouncing_the_same_cheque_reverse_the_money_once(install_b, relay):
    """THE B2 FIELD CASE, end to end. A and B both hold a `pending` cheque;
    EACH bounces it independently BEFORE either pulls the other's bounce;
    then both sync fully. Exactly ONE active reversal payments row must
    exist on BOTH devices afterward.
    MUTATION: give the money leg a fresh uuid4 instead of `money_uid`'s
    deterministic uid (revert the `pay_uid` parameter on `_record_payment`)
    -> two reversal rows on every device, AR over-restored by the face value
    -> RED. This is the single test that would have caught the submitted
    design (see this file's own module docstring).
    """
    admin, cid, pid = _new_shop(price=400.0)
    cust_id = _make_credit_customer(admin, 400.0, pid)
    create_r = admin.post(f'{API}/cheques', json={
        'direction': 'in', 'party_id': cust_id, 'amount': 400.0,
        'cheque_number': f'CHQ-{uuid.uuid4().hex[:8]}', 'due_date': '2026-12-01',
    })
    assert create_r.status_code == 201, create_r.get_json()
    cheque_id = create_r.get_json()['data']['id']

    # service_a needs a local_company_id_provider TOO in this test (unlike
    # retail_money_sync_test.py's own service_a, which never pulls -- A only
    # ever pushed there). Here A also PULLS device B's independent bounce,
    # so apply_pull_result needs to know which company_id to file it under
    # on A's side, exactly as B's provider does on B's side.
    service_a = SyncService(client_factory=lambda: relay, get_conn=get_retail_conn,
                            local_company_id_provider=lambda: cid)
    service_b = SyncService(client_factory=lambda: relay, get_conn=install_b,
                            local_company_id_provider=lambda: cid)

    # Get the 'created' cheque onto B FIRST -- both devices must observe the
    # SAME pending cheque before either bounces it, which is what makes the
    # next step a genuine race rather than one device acting on stale data.
    service_a.push_once()
    service_b.pull_once()
    b_conn = install_b()
    try:
        b_admin_id = b_conn.execute("SELECT id FROM cheques WHERE id=?", (cheque_id,)).fetchone()
        assert b_admin_id is not None, 'device B must hold the cheque before the race below'
    finally:
        b_conn.close()

    # THE RACE: A bounces via its own real route; B bounces the SAME cheque
    # via the same route logic against its own connection -- NEITHER has
    # seen the other's bounce yet.
    admin_user_row = registry_conn().execute(
        "SELECT id FROM users WHERE company_id=? LIMIT 1", (cid,)).fetchone()
    a_bounce = admin.post(f'{API}/cheques/{cheque_id}/bounce', json={'reason': 'Insufficient funds (A)'})
    assert a_bounce.status_code == 200, a_bounce.get_json()
    b_bounce = _bounce_on_device_b(install_b, cid, admin_user_row['id'], cheque_id, 'Insufficient funds (B)')
    assert b_bounce['status'] == 'success', b_bounce

    # Both push, both pull -- full convergence.
    service_a.push_once()
    service_b.push_once()
    service_a.pull_once()
    service_b.pull_once()
    # A second round: each device's own bounce event/payment was pushed
    # AFTER the other's pull_once() above in program order for one of the
    # two, so one more push/pull pair guarantees both have fully exchanged
    # everything regardless of which happened to go first.
    service_a.push_once()
    service_b.push_once()
    service_a.pull_once()
    service_b.pull_once()

    # ── Device A ──────────────────────────────────────────────────────────
    a_conn = get_retail_conn()
    try:
        a_events = a_conn.execute(
            "SELECT id, from_status, to_status, created_at_utc FROM cheque_events WHERE cheque_id=?",
            (cheque_id,)).fetchall()
        a_payments = [dict(r) for r in a_conn.execute(
            "SELECT * FROM payments WHERE party_id=? AND related_type='cheque' "
            "AND COALESCE(status,'active')='active' ORDER BY id", (cust_id,)).fetchall()]
    finally:
        a_conn.close()
    a_result = cheque_engine.fold(a_events)
    assert a_result.status == 'bounced', 'device A must have converged to bounced'
    a_reversals = [p for p in a_payments if p['direction'] == 'out']
    assert len(a_reversals) == 1, (
        f"device A must have EXACTLY ONE reversal payments row after both devices bounced "
        f"the same cheque, got {len(a_reversals)}: {a_reversals}")

    # ── Device B ──────────────────────────────────────────────────────────
    b_result = _b_folded_status(install_b, cheque_id)
    assert b_result.status == 'bounced', 'device B must have converged to bounced'
    b_payments = _b_active_cheque_payments(install_b, cust_id)
    b_reversals = [p for p in b_payments if p['direction'] == 'out']
    assert len(b_reversals) == 1, (
        f"device B must have EXACTLY ONE reversal payments row after both devices bounced "
        f"the same cheque, got {len(b_reversals)}: {b_reversals}")

    # THE money_uid claim, made explicit: both devices' single surviving
    # reversal row carries the IDENTICAL uid -- the deterministic function of
    # (cheque_id, crossing_index), never two different uuid4s.
    assert a_reversals[0]['uid'] == b_reversals[0]['uid']

    # AR is restored by EXACTLY the face value on both devices, not double.
    a_customer_balance = get_retail_conn().execute(
        "SELECT credit_balance FROM customers WHERE id=?", (cust_id,)).fetchone()[0]
    assert a_customer_balance == 400.0, (
        f"AR must be restored by exactly one face value (400.0), got {a_customer_balance} -- "
        f"a value of 800.0 here is the exact B2 double-apply defect this test exists to catch")

    # get_cheque's own timeline shows one effective and one INERT bounce event.
    detail = admin.get(f'{API}/cheques/{cheque_id}').get_json()['data']
    bounce_events = [e for e in detail['events'] if e['event_type'] == 'bounced']
    assert len(bounce_events) == 2, bounce_events
    effective_flags = sorted(e['effective'] for e in bounce_events)
    assert effective_flags == [False, True], (
        f"exactly one of the two bounce events must be effective, the other superseded: {bounce_events}")


# ── 11. A v31 peer that upgrades still receives the cheque events ──────────

def test_a_v31_peer_that_upgrades_still_receives_the_cheques(install_b, relay):
    """Device B runs a build whose RETAIL_SYNC_ENTITY_TYPES has no cheque
    types (simulated via SyncService's own `handled_entity_types` override --
    the exact mechanism that set exists to test). A creates and bounces; B
    pulls, SKIPPING both cheque events (assert the skip happened -- the
    cursor must still advance past them, `_apply_event`'s own documented
    posture for an entity type outside `handled_entity_types`). Then B
    'upgrades' (a fresh SyncService with the full entity-type set) and, per
    the v32 migration's own fix, gets a full re-pull because its stored
    `sync_cursor.relay_url` was reset to NULL -- simulated directly here
    (this file's harness has no real HTTP relay whose URL could change), and
    a second pull must now deliver both cheque events.
    MUTATION: skip the relay_url reset simulation (i.e. leave last_seq where
    the v31-shaped pull left it, no full re-pull) -> B never receives the
    cheque -> RED.
    """
    admin, cid, pid = _new_shop(price=90.0)
    cust_id = _make_credit_customer(admin, 90.0, pid)
    create_r = admin.post(f'{API}/cheques', json={
        'direction': 'in', 'party_id': cust_id, 'amount': 90.0,
        'cheque_number': f'CHQ-{uuid.uuid4().hex[:8]}', 'due_date': '2026-12-01',
    })
    cheque_id = create_r.get_json()['data']['id']
    admin.post(f'{API}/cheques/{cheque_id}/bounce', json={'reason': 'Insufficient funds'})

    service_a = SyncService(client_factory=lambda: relay, get_conn=get_retail_conn)
    v31_types = RETAIL_SYNC_ENTITY_TYPES - {'cheque', 'cheque_event'}
    service_b_v31 = SyncService(client_factory=lambda: relay, get_conn=install_b,
                                local_company_id_provider=lambda: cid,
                                handled_entity_types=v31_types)

    service_a.push_once()
    cursor_before = install_b().execute("SELECT last_seq FROM sync_cursor WHERE id=1").fetchone()[0]
    service_b_v31.pull_once()
    cursor_after_v31_pull = install_b().execute("SELECT last_seq FROM sync_cursor WHERE id=1").fetchone()[0]
    assert cursor_after_v31_pull > cursor_before, (
        'the v31 peer must still advance its cursor PAST the cheque events -- '
        '_apply_event returns True (fully handled) for any entity type outside '
        'handled_entity_types, never an error, never quarantined')

    b_conn = install_b()
    try:
        n_cheques_v31 = b_conn.execute("SELECT COUNT(*) FROM cheques").fetchone()[0]
    finally:
        b_conn.close()
    assert n_cheques_v31 == 0, 'a v31 peer must not have received the cheque at all -- it has no branch for it'

    # THE v32 MIGRATION'S OWN FIX, simulated directly: relay_url reset to
    # NULL, which the real `ensure_cursor_matches_relay` (called from inside
    # `pull_once` before the cursor is read) treats as "different from any
    # real relay" and resets last_seq to 0 on the very next pull. This
    # harness's InMemoryRelay carries no URL of its own to actually change,
    # so the reset is applied directly to sync_cursor -- exactly what the
    # real migration does, and exactly what the real ensure_cursor_matches_
    # relay would trigger given a NULL relay_url on this device's next pull.
    b_conn = install_b()
    try:
        b_conn.execute("UPDATE sync_cursor SET last_seq=0 WHERE id=1")
        b_conn.commit()
    finally:
        b_conn.close()

    service_b_v32 = SyncService(client_factory=lambda: relay, get_conn=install_b,
                                local_company_id_provider=lambda: cid)
    service_b_v32.pull_once()

    b_conn = install_b()
    try:
        b_cheque = b_conn.execute("SELECT * FROM cheques WHERE id=?", (cheque_id,)).fetchone()
        b_events = b_conn.execute("SELECT COUNT(*) FROM cheque_events WHERE cheque_id=?", (cheque_id,)).fetchone()[0]
        b_payments = b_conn.execute(
            "SELECT COUNT(*) FROM payments WHERE party_id=? AND related_type='cheque' "
            "AND COALESCE(status,'active')='active'", (cust_id,)).fetchone()[0]
    finally:
        b_conn.close()
    assert b_cheque is not None, 'the upgraded device must now hold the cheque header'
    assert b_events == 2, 'and both events (created, bounced)'
    assert b_payments == 2, 'and both money legs'
