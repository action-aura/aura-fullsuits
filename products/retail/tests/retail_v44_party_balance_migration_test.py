"""Aura Retail -- schema v44 migration: `party_balance_adjustments`,
`party_balance_upgrade_report`, `idx_returns_sale_id`, and the upgrade CARRY
(plan job f8252b48, T3).

THE RULE BEING PINNED. Balances are now derived from synced records
(core/retail/party_balance.py). A shop whose stored `credit_balance` was not
explained by its own records -- an imported opening balance, say -- would
silently LOSE that figure at the switch. So the upgrade of a SINGLE-computer
shop carries the difference forward as one fixed-uid, synced, append-only
adjustment per party (no GL journal: the v38 opening journal already booked
it). A NETWORKED shop gets no carry -- its per-computer stored figures were
each wrong about the shop -- and its old figures survive only in the local
upgrade report.

The v43 shape is manufactured the way retail_day1_v43_migration_test.py does
it: a real freshly built install, then the v44 objects dropped and
`PRAGMA user_version = 43` stamped. The upgrade itself runs through the REAL
`ensure_schema_version` chain (so the pre/post integrity check and the backup
are exercised, not bypassed).

Both directions of every deny/allow: carry vs no carry, one row vs two on a
second computer, trigger refuses content change vs allows a company rebind.

Run:
    pytest products/retail/tests/retail_v44_party_balance_migration_test.py -q
"""
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import uuid
from decimal import Decimal
from pathlib import Path

import pytest

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

CO = 'co-v44'
_TMP_DIRS = []


def teardown_module(module):
    for d in _TMP_DIRS:
        shutil.rmtree(d, ignore_errors=True)
    _TMP_DIRS.clear()


@pytest.fixture
def registry(monkeypatch, tmp_path):
    """An isolated registry.db with the `devices` table (so the single-computer
    test is answerable) and no devices yet."""
    from commercial_runtime.identity import device_registry, registry_db
    reg_dir = tmp_path / 'registry_db'
    reg_dir.mkdir()
    monkeypatch.setattr(registry_db, '_db_dir', str(reg_dir))
    monkeypatch.setattr(registry_db, 'DB_PATH', str(reg_dir / 'registry.db'))
    conn = registry_db.get_conn()
    device_registry.apply_identity_device_schema(conn)
    conn.commit()
    conn.close()
    return registry_db


def _add_devices(registry_db, n):
    conn = registry_db.get_conn()
    for i in range(n):
        conn.execute("INSERT INTO devices (id, company_id, first_seen_at, status) VALUES (?,?,?, 'active')",
                     (f'dev-{uuid.uuid4().hex[:6]}-{i}', CO, '2026-10-05T00:00:00Z'))
    conn.commit()
    conn.close()


def _install():
    tmp = tempfile.mkdtemp(prefix='aura-retail-v44-')
    _TMP_DIRS.append(tmp)
    os.environ.update({'AURA_APP_DATA': tmp, 'AURA_STANDALONE': '1', 'AURA_SITE_RELAY_ENABLED': '0'})
    import database.schema as sch
    sch.BASE_DIR = os.path.join(tmp, 'database')
    sch.SUBSYS_DIR = os.path.join(sch.BASE_DIR, 'subsystems')
    sch.init_retail()
    return sch, os.path.join(sch.SUBSYS_DIR, 'retail.db')


def _open(db):
    conn = sqlite3.connect(db, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def _make_v43(db, last_seq=0, ids=None):
    """A v43-shaped database for company CO with two customers and one
    supplier:
        cust_over   stored +4.000, one 'in' payment of 3.000   -> derived -3.000, carry +7.000
        cust_ok     stored -3.000, one 'in' payment of 3.000   -> derived -3.000, no drift
        sup_over    stored -2.500, no records                  -> derived  0.000, carry -2.500
    `ids` lets a second computer hold the SAME party ids (a shop whose
    customers synced). Returns the ids."""
    import api.retail_api as ra
    ids = ids or {'cust_over': str(uuid.uuid4()), 'cust_ok': str(uuid.uuid4()), 'sup_over': str(uuid.uuid4())}
    conn = _open(db)
    try:
        previous = ra._CREDIT_SCHEMA_READY
        ra._CREDIT_SCHEMA_READY = False
        try:
            ra._ensure_credit_schema(conn)
        finally:
            ra._CREDIT_SCHEMA_READY = previous
        conn.execute('DROP TABLE IF EXISTS party_balance_adjustments')
        conn.execute('DROP TABLE IF EXISTS party_balance_upgrade_report')
        conn.execute('DROP INDEX IF EXISTS idx_returns_sale_id')
        for key, name, bal in (('cust_over', 'Over', 4.0), ('cust_ok', 'Ok', -3.0)):
            conn.execute("INSERT INTO customers (id, company_id, name, credit_balance) VALUES (?,?,?,?)",
                         (ids[key], CO, name, bal))
            conn.execute(
                "INSERT INTO payments (company_id, method, amount, party_type, party_id, direction, related_type, status) "
                "VALUES (?, 'cash', 3.0, 'customer', ?, 'in', NULL, 'active')", (CO, ids[key]))
        conn.execute("INSERT INTO suppliers (id, company_id, name, credit_balance) VALUES (?,?,?,?)",
                     (ids['sup_over'], CO, 'SupOver', -2.5))
        conn.execute("UPDATE sync_cursor SET last_seq=? WHERE id=1", (last_seq,))
        conn.execute('PRAGMA user_version = 43')
        conn.commit()
    finally:
        conn.close()
    return ids


def _migrate(sch, db):
    from commercial_runtime.security.migration_safety import ensure_schema_version
    backup_dir = tempfile.mkdtemp(prefix='aura-retail-v44-backup-')
    _TMP_DIRS.append(backup_dir)
    conn = _open(db)
    try:
        ensure_schema_version(conn, db, sch.RETAIL_SCHEMA_VERSION, sch._migrate_retail_schema, backup_dir)
    finally:
        conn.close()
    return backup_dir


def _rows(db, sql, params=()):
    conn = _open(db)
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def _carry_uid(kind, pid):
    from core.retail import party_balance as pb
    return pb.carry_uid(kind, pid)


# ── single computer: balances survive, one fixed-uid carry per drifted party ──

def test_single_computer_upgrade_carries_the_difference_once_and_keeps_every_balance(registry):
    _add_devices(registry, 1)
    sch, db = _install()
    ids = _make_v43(db)
    backup_dir = _migrate(sch, db)

    # v45 UPDATE: head moved to 45 (W4a purchase-order sync); this file still
    # proves v44's own capture, and the head tripwire moved to
    # retail_v45_po_sync_migration_test.py.
    # v46 UPDATE: head moved to 46 (W4a-2 partial receipts); same caveat -- this
    # can no longer catch the head drifting past 46 (that tripwire lives with
    # retail_v46_po_receipts_migration_test.py).
    assert sch.RETAIL_SCHEMA_VERSION == 56  # v56 UPDATE: head moved 54 -> 56 (SYNC-BOOT Phase 2; v55 is MONEY-5's and is not on this line of history); v56's own shape is pinned by retail_site_stream_cursors_migration_test. Same exact-number pin as before.  # v54 UPDATE: head moved 53 -> 54 (UI-3a); v54's own shape is pinned by retail_ui3a_sync_migration_test. [earlier: v53 UPDATE: head moved 52 -> 53 (MONEY-4c); v53's own shape is pinned by retail_money4c_credit_inherit_test.py. (was:  head moved 51 -> 52 (MONEY-4b); v52's own shape is pinned by retail_money4b_migration_test.py | prior: v51 UPDATE: head moved 50 -> 51 (MONEY-3); v51's own shape is pinned by retail_money3_cheque_books_test.py / retail_money3_supplier_drawer_test.py | prior: v50 UPDATE: head moved 49 -> 50 (W2b/W3a); v50's own shape is pinned by retail_v50_config_sync_migration_test.py)]
    assert _rows(db, 'PRAGMA user_version')[0]['user_version'] == 56  # v56 UPDATE: head moved 54 -> 56 (SYNC-BOOT Phase 2; v55 is MONEY-5's and is not on this line of history); v56's own shape is pinned by retail_site_stream_cursors_migration_test. Same exact-number pin as before.  # v54 UPDATE: head moved 53 -> 54 (UI-3a); v54's own shape is pinned by retail_ui3a_sync_migration_test. [earlier: v53 UPDATE: head moved 52 -> 53 (MONEY-4c); v53's own shape is pinned by retail_money4c_credit_inherit_test.py. (was:  head moved 51 -> 52 (MONEY-4b); v52's own shape is pinned by retail_money4b_migration_test.py | prior: v51 UPDATE: head moved 50 -> 51 (MONEY-3); v51's own shape is pinned by retail_money3_cheque_books_test.py / retail_money3_supplier_drawer_test.py | prior: v50 UPDATE: head moved 49 -> 50 (W2b/W3a); v50's own shape is pinned by retail_v50_config_sync_migration_test.py)]
    assert _rows(db, 'PRAGMA integrity_check')[0]['integrity_check'] == 'ok'
    assert os.listdir(backup_dir), 'ensure_schema_version must have written its pre-migration backup'

    from core.retail import party_balance as pb
    conn = _open(db)
    try:
        # balances unchanged: the stored cache was not touched, and the derived
        # figure (records + carry) now equals it for EVERY party.
        for table, kind, key, stored in (('customers', 'customer', 'cust_over', 4.0),
                                         ('customers', 'customer', 'cust_ok', -3.0),
                                         ('suppliers', 'supplier', 'sup_over', -2.5)):
            assert conn.execute(f"SELECT credit_balance FROM {table} WHERE id=?", (ids[key],)).fetchone()[0] == stored
            derived = pb.customer_balance(conn, CO, ids[key], 'JOD') if kind == 'customer' \
                else pb.supplier_balance(conn, CO, ids[key], 'JOD')
            assert float(derived) == stored, (key, derived, stored)
        carries = [dict(r) for r in conn.execute(
            "SELECT uid, company_id, party_type, party_id, amount, reason FROM party_balance_adjustments ORDER BY party_type")]
    finally:
        conn.close()

    by_party = {c['party_id']: c for c in carries}
    assert set(by_party) == {ids['cust_over'], ids['sup_over']}, 'cust_ok did not drift, so it must have no carry'
    assert by_party[ids['cust_over']]['amount'] == 7.0 and by_party[ids['cust_over']]['party_type'] == 'customer'
    assert by_party[ids['sup_over']]['amount'] == -2.5 and by_party[ids['sup_over']]['party_type'] == 'supplier'
    # the uid is the FIXED function of (type, party) with no company in it
    assert by_party[ids['cust_over']]['uid'] == _carry_uid('customer', ids['cust_over'])
    assert by_party[ids['sup_over']]['uid'] == _carry_uid('supplier', ids['sup_over'])
    assert all(c['reason'] == 'upgrade_v44_carry' and c['company_id'] == CO for c in carries)

    report = {r['party_id']: r for r in _rows(db, "SELECT * FROM party_balance_upgrade_report")}
    assert set(report) == {ids['cust_over'], ids['sup_over']}
    r = report[ids['cust_over']]
    assert (r['stored_before'], r['derived'], r['carried'], r['classification']) == (4.0, -3.0, 7.0, 'carried')

    # a sync event per carry, with the full payload and NO company_id
    events = _rows(db, "SELECT entity_type, entity_id, event_type, payload FROM sync_outbox "
                       "WHERE entity_type='party_balance_adjustment'")
    assert len(events) == 2 and all(e['event_type'] == 'create' for e in events)
    payload = {json.loads(e['payload'])['party_id']: json.loads(e['payload']) for e in events}
    assert payload[ids['cust_over']]['amount'] == 7.0
    assert payload[ids['cust_over']]['uid'] == by_party[ids['cust_over']]['uid']
    assert 'company_id' not in payload[ids['cust_over']]

    # NO GL journal was posted for the carry (the v38 opening journal booked it)
    gl = [r for r in _rows(db, "SELECT source_type FROM gl_journals") if r['source_type'] == 'party_balance_carry']
    assert gl == []

    idx = _rows(db, "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_returns_sale_id'")
    assert idx, 'idx_returns_sale_id must exist after the upgrade'


def test_rerunning_the_chain_or_the_step_adds_nothing(registry):
    _add_devices(registry, 1)
    sch, db = _install()
    ids = _make_v43(db)
    _migrate(sch, db)
    before = (_rows(db, "SELECT uid, amount FROM party_balance_adjustments ORDER BY uid"),
              _rows(db, "SELECT * FROM party_balance_upgrade_report ORDER BY party_id"),
              _rows(db, "SELECT COUNT(*) AS n FROM sync_outbox WHERE entity_type='party_balance_adjustment'"))
    conn = _open(db)
    try:
        # Forced worst case: user_version says v43 again, so capture RUNS a
        # second time. The carry already counts toward the derived figure, so
        # there is nothing left to carry.
        conn.execute('PRAGMA user_version = 43')
        sch._migrate_add_party_balance_ledger(conn)
        conn.execute('PRAGMA user_version = 44')
        conn.commit()
        # A cache that has merely DRIFTED since the upgrade (a crash between a
        # write and its refresh) must never be mistaken for an opening balance
        # by a later chain replay: only a genuine v43 -> v44 upgrade captures.
        conn.execute("UPDATE customers SET credit_balance=99 WHERE id=?", (ids['cust_ok'],))
        conn.commit()
        sch._migrate_retail_schema(conn)                      # whole chain, at v44: capture skipped
        sch._migrate_add_party_balance_ledger(conn)           # the step itself
        conn.commit()
    finally:
        conn.close()
    after = (_rows(db, "SELECT uid, amount FROM party_balance_adjustments ORDER BY uid"),
             _rows(db, "SELECT * FROM party_balance_upgrade_report ORDER BY party_id"),
             _rows(db, "SELECT COUNT(*) AS n FROM sync_outbox WHERE entity_type='party_balance_adjustment'"))
    assert after == before
    assert len(before[0]) == 2 and len(ids) == 3


# ── networked: no carry, the old figures survive only as a record ─────────────

@pytest.mark.parametrize('how', ['two_devices', 'has_pulled', 'registry_unreadable'])
def test_a_networked_or_unprovable_shop_gets_no_carry_and_keeps_its_old_figures_as_a_record(registry, how):
    if how == 'two_devices':
        _add_devices(registry, 2)
    elif how == 'has_pulled':
        _add_devices(registry, 1)
    else:
        conn = registry.get_conn()
        conn.execute('DROP TABLE devices')       # an unreadable registry reads as "networked"
        conn.commit()
        conn.close()
    sch, db = _install()
    ids = _make_v43(db, last_seq=5 if how == 'has_pulled' else 0)
    _migrate(sch, db)

    assert _rows(db, "SELECT * FROM party_balance_adjustments") == []
    assert _rows(db, "SELECT COUNT(*) AS n FROM sync_outbox WHERE entity_type='party_balance_adjustment'")[0]['n'] == 0
    report = {r['party_id']: r for r in _rows(db, "SELECT * FROM party_balance_upgrade_report")}
    assert set(report) == {ids['cust_over'], ids['sup_over']}
    assert all(r['classification'] == 'networked_no_carry' and r['carried'] == 0.0 for r in report.values())
    assert report[ids['cust_over']]['stored_before'] == 4.0     # the old figure is kept as a record
    from core.retail import party_balance as pb
    conn = _open(db)
    try:
        # ...and the balance is the shop-wide derived figure, not the old one.
        assert pb.customer_balance(conn, CO, ids['cust_over'], 'JOD') == Decimal('-3.000')
        assert pb.supplier_balance(conn, CO, ids['sup_over'], 'JOD') == Decimal('0.000')
    finally:
        conn.close()


# ── two computers that BOTH believed they were alone: one carry per party ─────

def test_two_computers_that_each_carried_converge_to_one_carry_per_party(registry):
    _add_devices(registry, 1)
    sch_a, db_a = _install()
    ids = _make_v43(db_a)
    _migrate(sch_a, db_a)
    sch_b, db_b = _install()
    # the SAME party ids on computer B, as a shop that synced customers would have
    _make_v43(db_b, ids=ids)
    _migrate(sch_b, db_b)

    from commercial_runtime.sync.sync_service import SyncService
    svc = SyncService(client_factory=lambda: None, get_conn=lambda: _open(db_b),
                      local_company_id_provider=lambda: CO)
    events = _rows(db_a, "SELECT entity_type, entity_id, event_type, payload FROM sync_outbox "
                         "WHERE entity_type='party_balance_adjustment'")
    assert len(events) == 2
    conn = _open(db_b)
    try:
        for e in events:
            ok = svc._apply_event(conn, {'entity_type': e['entity_type'], 'event_type': e['event_type'],
                                         'entity_id': e['entity_id'], 'payload': json.loads(e['payload'])},
                                  local_company_id=CO)
            assert ok is True
        conn.commit()
    finally:
        conn.close()
    rows = _rows(db_b, "SELECT party_id, uid, amount FROM party_balance_adjustments ORDER BY party_id")
    assert len(rows) == 2, f"two computers' carries must collapse to one fact per party, got {rows}"
    assert {r['party_id'] for r in rows} == {ids['cust_over'], ids['sup_over']}


# ── the append-only guard: deny content change/delete, allow a company rebind ─

def test_adjustments_are_append_only_but_a_company_rebind_is_allowed(registry):
    _add_devices(registry, 1)
    sch, db = _install()
    _make_v43(db)
    _migrate(sch, db)
    conn = _open(db)
    try:
        uid = conn.execute("SELECT uid FROM party_balance_adjustments LIMIT 1").fetchone()[0]
        # ALLOW: rebind_company_id's bare UPDATE of company_id
        conn.execute("UPDATE party_balance_adjustments SET company_id='rebound' WHERE uid=?", (uid,))
        assert conn.execute("SELECT company_id FROM party_balance_adjustments WHERE uid=?", (uid,)).fetchone()[0] == 'rebound'
        # DENY: changing the money, and deleting the row
        with pytest.raises(sqlite3.DatabaseError, match='append-only'):
            conn.execute("UPDATE party_balance_adjustments SET amount=amount+1 WHERE uid=?", (uid,))
        with pytest.raises(sqlite3.DatabaseError, match='append-only'):
            conn.execute("DELETE FROM party_balance_adjustments WHERE uid=?", (uid,))
        # the table is discoverable by the rebind (carries company_id)
        assert 'party_balance_adjustments' in sch.company_scoped_tables(conn)
    finally:
        conn.close()
