"""Aura Retail -- MONEY-4b (retail schema v52): the catch-up for history.

Every drawer paid-out made BEFORE v52 reached no book (review defect D2). The v52 step gives each
existing manual paid-out ONE journal -- Dr 6900 Other Expenses / Cr 1000 -- at the movement's own
BUSINESS date (not the upgrade day), keyed on the movement's id so it can never post twice.
Their purpose was never captured, so 6900 is the honest bucket (ROADMAP.md says so).
The paid_in half (added to the still-unreleased v52 in place): each existing manual paid_in gets
ONE journal Dr 1000 / Cr 4800 Other Income at its own business date, keyed the same way, and that
catch-up runs on EVERY run of the step (not only on the 51 -> 52 upgrade) so a database that
already ran the first shape of v52 still gets its paid_ins booked -- once.

The fixture builds what a v51 database really holds: manual paid-out rows with no journal and no
account column. (The live route now posts, and `gl_journals` is append-only by trigger, so the
history is written as rows, exactly as v51 wrote them, and the column/table the step adds are
dropped to put the schema back at v51.)

WHAT THIS FILE PROVES
  * upgrading 51 -> 52 posts exactly ONE journal per manual paid-out, Dr 6900 / Cr 1000, at the
    movement's business date (start hour 6: a 01:30 paid-out belongs to the day before);
  * it posts NOTHING for float_in / float_out, nor for the drawer paid-out a supplier payment made
    nor the paid_in its cancellation made (`payment_uid` set -- the payment already booked its
    own journals; booking the movement too would count the money twice);
  * every manual paid_in gets ONE journal Dr 1000 / Cr 4800 at its business date;
  * a database ALREADY at 52 whose paid_outs were caught up by the first shape of the step gets
    its paid_ins booked on the next run, exactly once, and the paid_outs are not touched;
  * replaying the chain, and calling the step again and again, posts nothing more;
  * a paid-out the live route already booked (6100) is not booked a second time by the step;
  * the company scope holds (each company's catch-up lands in its own books);
  * the trial balance balances, the version is 52, the migration-safety integrity check passes
    (it runs inside `ensure_schema_version`), and the new column + table exist.

MUTATIONS (each performed, red quoted in the hand-back):
  M-a  the step's `post_drawer_paid_out` call removed       -> test_upgrade_posts_one_journal_per_manual_paid_out
  M-b  `_post_once` existence check removed (not idempotent) -> test_replaying_the_step_posts_nothing_more
  M-c  drop `m.payment_uid IS NULL` from the step's SELECT   -> test_supplier_drawer_paid_outs_float_and_paid_in_are_not_booked
  M-d  date by `_today()` instead of the movement's business date -> test_upgrade_posts_one_journal_per_manual_paid_out
  M-e  `_post_once` existence check removed (paid_in catch-up non-idempotent)
       -> test_replaying_the_step_posts_nothing_more and test_a_database_that_already_ran_the_first_v52_gets_its_paid_ins_once
  M-f  paid_in catch-up gated on the upgrade version again -> test_a_database_that_already_ran_the_first_v52_gets_its_paid_ins_once

Run (one file per process):
    pytest products/retail/tests/retail_money4b_migration_test.py -v
"""
import shutil
import sqlite3
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

import retail_accountant_regate_test as H  # noqa: E402  (builds the isolated app + data dir)
import database.schema as sch  # noqa: E402
from core.retail import general_ledger as gl  # noqa: E402

API = H.API


def _rows(sql, params=()):
    conn = H.get_retail_conn()
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def _put_movement(session_id, mtype, amount, created_at, payment_uid=None):
    mid = str(uuid.uuid4())
    conn = H.get_retail_conn()
    try:
        conn.execute(
            "INSERT INTO cash_movements (id, session_id, type, amount, reason, created_by, created_at, "
            "payment_uid) VALUES (?,?,?,?,?,?,?,?)",
            (mid, session_id, mtype, amount, 'history', 'u', created_at, payment_uid))
        conn.commit()
    finally:
        conn.close()
    return mid


def _to_v51_shape():
    """What a v51 install holds: no expenses table, no gl_account_code column, user_version 51."""
    conn = H.get_retail_conn()
    try:
        conn.execute("DROP TABLE IF EXISTS expenses")
        cols = {r[1] for r in conn.execute("PRAGMA table_info(cash_movements)").fetchall()}
        if 'gl_account_code' in cols:
            conn.execute("ALTER TABLE cash_movements DROP COLUMN gl_account_code")
        conn.execute("PRAGMA user_version = 51")
        conn.commit()
        return conn.execute("PRAGMA database_list").fetchone()[2]
    finally:
        conn.close()


def _migrate(db):
    from commercial_runtime.security.migration_safety import ensure_schema_version
    backup_dir = tempfile.mkdtemp(prefix='aura-m4b-backup-')
    conn = sqlite3.connect(db, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        ensure_schema_version(conn, db, sch.RETAIL_SCHEMA_VERSION, sch._migrate_retail_schema, backup_dir)
    finally:
        conn.close()
        shutil.rmtree(backup_dir, ignore_errors=True)


def _catchup_journals(cid):
    out = []
    for j in _rows("SELECT id, business_date AS date, source_uid, uid FROM gl_journals "
                   "WHERE company_id=? AND source_type='cash_movement' ORDER BY business_date, id", (cid,)):
        lines = {r['code']: (r['d'], r['c']) for r in _rows(
            "SELECT a.code AS code, l.debit AS d, l.credit AS c FROM gl_journal_lines l "
            "JOIN gl_accounts a ON a.id=l.account_id WHERE l.journal_id=?", (j['id'],))}
        out.append({**j, 'lines': lines})
    return out


def _history(admin, cid, with_paid_in=True):
    """A drawer with history. Returns (session_id, {name: movement id})."""
    r = admin.post(f'{API}/settings/business-day', json={'business_day_start_hour': 6})
    assert r.status_code == 200, r.get_json()
    sid = H._open(admin, 100.0).get_json()['data']['id']
    ids = {
        'noon': _put_movement(sid, 'paid_out', 20.0, '2026-03-10 14:00:00'),
        # 01:30 on the 11th with a 06:00 day start: it belongs to business day 2026-03-10
        'night': _put_movement(sid, 'paid_out', 7.5, '2026-03-11 01:30:00'),
        'later': _put_movement(sid, 'paid_out', 3.0, '2026-03-12 09:00:00'),
        'float_in': _put_movement(sid, 'float_in', 50.0, '2026-03-10 09:00:00'),
        'float_out': _put_movement(sid, 'float_out', 10.0, '2026-03-10 18:00:00'),
        # the paid_in a CANCELLED drawer supplier payment made: booked by that payment's reversal
        'supplier_in': _put_movement(sid, 'paid_in', 99.0, '2026-03-10 17:00:00', payment_uid=str(uuid.uuid4())),
        # the paid-out a drawer supplier payment made: its payment booked Dr AP / Cr Cash itself
        'supplier': _put_movement(sid, 'paid_out', 99.0, '2026-03-10 16:00:00', payment_uid=str(uuid.uuid4())),
    }
    if with_paid_in:
        ids['paid_in'] = _put_movement(sid, 'paid_in', 15.0, '2026-03-10 15:00:00')
        # 01:30 on the 12th, start hour 6 -> business day 2026-03-11
        ids['paid_in_night'] = _put_movement(sid, 'paid_in', 4.0, '2026-03-12 01:30:00')
    return sid, ids


def test_upgrade_posts_one_journal_per_manual_paid_out():
    admin, cid, _pid = H._new_shop()
    _sid, ids = _history(admin, cid)
    db = _to_v51_shape()
    assert _catchup_journals(cid) == [], 'fixture is not a v51 shape: journals already exist'

    _migrate(db)

    got = _catchup_journals(cid)
    want = {ids['noon']: ('2026-03-10', 20.0), ids['night']: ('2026-03-10', 7.5),
            ids['later']: ('2026-03-12', 3.0)}
    want_in = {ids['paid_in']: ('2026-03-10', 15.0), ids['paid_in_night']: ('2026-03-11', 4.0)}
    assert len(got) == 5, got                      # a count, so "no journal matched" cannot pass
    for j in got:
        if j['source_uid'] in want_in:
            date, amount = want_in[j['source_uid']]
            assert j['lines'] == {'1000': (amount, 0.0), '4800': (0.0, amount)}, j
        else:
            date, amount = want[j['source_uid']]
            assert j['lines'] == {'6900': (amount, 0.0), '1000': (0.0, amount)}, j
        assert j['date'] == date, j
        assert j['uid'] == gl.cash_movement_journal_uid(j['source_uid'])
    assert {j['source_uid'] for j in got} == set(want) | set(want_in)
    # the upgraded database is at 52, balanced, and carries the new shape
    assert _rows("PRAGMA user_version")[0]['user_version'] == sch.RETAIL_SCHEMA_VERSION == 57  # v56 UPDATE: head moved 54 -> 56 (SYNC-BOOT Phase 2; v55 is MONEY-5's and is not on this line of history); v56's own shape is pinned by retail_site_stream_cursors_migration_test. Same exact-number pin as before.  # v54 UPDATE: head moved 53 -> 54 (UI-3a); v54's own shape is pinned by retail_ui3a_sync_migration_test. [earlier: v53 UPDATE: head moved 52 -> 53 (MONEY-4c); (was 52)]  # v57 UPDATE: head moved 56 -> 57 (STOCK-TAKE, ROADMAP v57 claim); same exact pin, not loosened.
    cols = {r['name'] for r in _rows("PRAGMA table_info(cash_movements)")}
    assert 'gl_account_code' in cols
    assert _rows("SELECT COUNT(*) AS n FROM sqlite_master WHERE name='expenses'")[0]['n'] == 1
    assert admin.get(f'{API}/reports/trial-balance').get_json()['data']['is_balanced'] is True
    pl = admin.get(f'{API}/reports/profit-and-loss?start_date=2026-03-10&end_date=2026-03-10').get_json()['data']
    assert {a['code']: a['amount'] for a in pl['operating_expenses']['accounts']} == {'6900': 27.5}, pl
    rev = admin.get(f'{API}/reports/profit-and-loss?start_date=2026-03-10&end_date=2026-03-11').get_json()['data']
    assert {a['code']: a['amount'] for a in rev['other_income']['accounts']}.get('4800') == 19.0, rev['other_income']['accounts']


def test_supplier_drawer_movements_and_floats_are_not_booked_manual_paid_ins_are():
    admin, cid, _pid = H._new_shop()
    _sid, ids = _history(admin, cid)
    db = _to_v51_shape()
    _migrate(db)
    booked = {j['source_uid'] for j in _catchup_journals(cid)}
    for name in ('float_in', 'float_out', 'supplier', 'supplier_in'):
        assert ids[name] not in booked, f'{name} must not be caught up'
    # control (the allow half): the three manual paid-outs and the two manual paid-ins ARE
    assert {ids['noon'], ids['night'], ids['later'], ids['paid_in'], ids['paid_in_night']} == booked


def test_replaying_the_step_posts_nothing_more():
    admin, cid, _pid = H._new_shop()
    _sid, _ids = _history(admin, cid)
    db = _to_v51_shape()
    _migrate(db)
    first = _catchup_journals(cid)
    assert len(first) == 5

    # (1) the whole chain re-entered (a failed launch): stamp 51 again and migrate
    conn = sqlite3.connect(db); conn.execute("PRAGMA user_version = 51"); conn.commit(); conn.close()
    _migrate(db)
    assert _catchup_journals(cid) == first

    # (2) the step called directly, again and again, on a connection stamped 51 (the entry
    # version gate is open): the movement key alone must hold the line
    conn = sqlite3.connect(db, timeout=30)
    try:
        conn.execute("PRAGMA user_version = 51")
        sch._migrate_add_money4b_expenses(conn)
        sch._migrate_add_money4b_expenses(conn)
        conn.commit()
    finally:
        conn.close()
    assert _catchup_journals(cid) == first
    n_outbox = _rows("SELECT COUNT(*) AS n FROM sync_outbox WHERE entity_type='gl_journal'")[0]['n']
    assert n_outbox >= 5          # the catch-up journals are announced to peers like any journal


def test_a_paid_out_the_live_route_already_booked_is_not_booked_again():
    admin, cid, _pid = H._new_shop()
    sid = H._open(admin, 100.0).get_json()['data']['id']
    r = admin.post(f'{API}/cash-sessions/{sid}/movements',
                   json={'type': 'paid_out', 'amount': 12, 'gl_account_code': '6100'})
    assert r.status_code == 200, r.get_json()
    r = admin.post(f'{API}/cash-sessions/{sid}/movements',
                   json={'type': 'paid_in', 'amount': 8, 'gl_account_code': '3100'})
    assert r.status_code == 200, r.get_json()
    before = _catchup_journals(cid)
    assert len(before) == 2 and {'6100', '3100'} <= {c for j in before for c in j['lines']}
    db = _to_v51_shape()          # as if the database were re-entered at 51 with the live row present
    _migrate(db)
    assert _catchup_journals(cid) == before, 'the step double-booked a movement the route had booked'


def test_each_companys_catch_up_lands_in_its_own_books():
    a, a_cid, _p = H._new_shop()
    b, b_cid, _q = H._new_shop()
    _s1, a_ids = _history(a, a_cid)
    _s2, b_ids = _history(b, b_cid)
    db = _to_v51_shape()
    _migrate(db)
    a_sources = {j['source_uid'] for j in _catchup_journals(a_cid)}
    b_sources = {j['source_uid'] for j in _catchup_journals(b_cid)}
    assert a_sources == {a_ids[k] for k in ('noon', 'night', 'later', 'paid_in', 'paid_in_night')}
    assert b_sources == {b_ids[k] for k in ('noon', 'night', 'later', 'paid_in', 'paid_in_night')}
    assert not (a_sources & b_sources)


def test_a_database_that_already_ran_the_first_v52_gets_its_paid_ins_once():
    """The first shape of v52 booked paid_outs only. Reproduce it: history WITHOUT paid_ins is
    migrated (the step's paid_out catch-up runs, stamp 52), THEN paid_ins exist unbooked, and the
    step runs again on the database already at 52. The paid_ins must be booked, once, the paid_outs
    must not be touched, and a third run posts nothing."""
    admin, cid, _pid = H._new_shop()
    _sid, ids = _history(admin, cid, with_paid_in=False)
    db = _to_v51_shape()
    _migrate(db)
    first = _catchup_journals(cid)
    assert len(first) == 3 and all('6900' in j['lines'] for j in first), first
    assert _rows("PRAGMA user_version")[0]['user_version'] == 57  # v56 UPDATE: head moved 54 -> 56 (SYNC-BOOT Phase 2; v55 is MONEY-5's and is not on this line of history); v56's own shape is pinned by retail_site_stream_cursors_migration_test. Same exact-number pin as before.  # v54 UPDATE: head moved 53 -> 54 (UI-3a); v54's own shape is pinned by retail_ui3a_sync_migration_test. [earlier: v53 UPDATE: the full chain now stamps 53 (MONEY-4c); (was 52)]  # v57 UPDATE: head moved 56 -> 57 (STOCK-TAKE, ROADMAP v57 claim); same exact pin, not loosened.

    sid = _rows("SELECT id FROM cash_sessions WHERE company_id=?", (cid,))[0]['id']
    p1 = _put_movement(sid, 'paid_in', 15.0, '2026-03-10 15:00:00')
    p2 = _put_movement(sid, 'paid_in', 4.0, '2026-03-12 01:30:00')
    _put_movement(sid, 'paid_in', 99.0, '2026-03-10 17:00:00', payment_uid=str(uuid.uuid4()))

    def run_step():
        conn = sqlite3.connect(db, timeout=30)
        try:
            assert conn.execute("PRAGMA user_version").fetchone()[0] == 57     # v56 UPDATE: head moved 54 -> 56 (SYNC-BOOT Phase 2; v55 is MONEY-5's and is not on this line of history); v56's own shape is pinned by retail_site_stream_cursors_migration_test. Same exact-number pin as before.  # v54 UPDATE: head moved 53 -> 54 (UI-3a); v54's own shape is pinned by retail_ui3a_sync_migration_test. [earlier: gate closed (v53 UPDATE: the stamp is now 53; the step's own gate is entry_user_version < 52, still closed)]  # v57 UPDATE: head moved 56 -> 57 (STOCK-TAKE, ROADMAP v57 claim); same exact pin, not loosened.
            sch._migrate_add_money4b_expenses(conn)
            conn.commit()
        finally:
            conn.close()

    run_step()
    got = _catchup_journals(cid)
    assert len(got) == 5, got
    assert [j for j in got if j['source_uid'] in {x['source_uid'] for x in first}] == first
    ins = {j['source_uid']: j for j in got if '4800' in j['lines']}
    assert set(ins) == {p1, p2}, ins
    assert ins[p1]['date'] == '2026-03-10' and ins[p1]['lines'] == {'1000': (15.0, 0.0), '4800': (0.0, 15.0)}
    assert ins[p2]['date'] == '2026-03-11' and ins[p2]['lines'] == {'1000': (4.0, 0.0), '4800': (0.0, 4.0)}
    run_step()
    assert _catchup_journals(cid) == got, 'a second run double-booked a paid_in'
    assert admin.get(f'{API}/reports/trial-balance').get_json()['data']['is_balanced'] is True


def test_the_step_leaves_a_minimal_database_alone():
    bare = sqlite3.connect(':memory:')
    sch._migrate_add_money4b_expenses(bare)           # no cash_movements, no ledger: must not raise
    assert bare.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='expenses'").fetchone()[0] == 1
    bare.close()
