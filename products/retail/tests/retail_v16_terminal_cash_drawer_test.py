"""
Aura Retail -- schema v16 regression coverage: the cash drawer stops belonging
to a BRANCH and starts belonging to a TERMINAL (launch-readiness Phase 4,
ROADMAP.md's 2026-08-21 reservation). See database/schema.py::
_migrate_bind_cash_drawer_to_terminal.

WHAT V16 CLAIMS, AND THEREFORE WHAT THIS FILE HAS TO PROVE.

schema v10 put a partial UNIQUE index on (company_id, branch_id) WHERE
status='open', so one branch gets exactly one open till session. A shop with a
desktop and a phone at the same counter therefore cannot open a second drawer
at all, and every sale the phone rings is stamped with whatever session IS open
on that branch -- the desktop's, because `_open_cash_session_id` matches on
company+branch+status and nothing else. The Z report at end of day then
reconciles one physical drawer against money that was never in it. v16 replaces
that index with UNIQUE(company_id, terminal_id) WHERE status='open'.

THIS IS THE FIRST NON-ADDITIVE MIGRATION IN THE CHAIN. Every step before it
created a table, added a column, or wrote rows. This one DROPS a unique index
and creates a different one, and `CREATE UNIQUE INDEX` is evaluated against the
rows that are already there. An install already holding two open sessions --
the ordinary multi-branch shop -- would make it raise, and `app.py::init_app()`
calls `init_retail()` unconditionally with no handler, so a raise there is a POS
that will not start. That is not hypothetical in this repository: it is what
the v15 gate did to a real shop.

THE SIX THINGS THIS FILE EXISTS TO HOLD DOWN:

  1. A REAL SHOP'S MONEY SURVIVES. Section 1 builds a two-year-old shop --
     three branches, twenty-four months of counted-and-closed shifts each,
     floats, variances, cash movements -- snapshots every money column on every
     session, migrates, and requires the snapshot back byte for byte. v15
     passed its own tests while turning shelves of 112/57/7 into -8/-3/-1 on
     exactly such a shop; a migration is not done when its unit tests pass, it
     is done when a realistic legacy fixture comes out holding what it went in
     holding.

  2. THE COLLISION IS RESOLVED BEFORE THE INDEX IS BUILT, NEVER BY IT.
     Section 2 covers the install with one open drawer per branch (the shape
     that collides), with terminal ids already stamped by v13's writers AND
     with the NULL history where the backfill is what creates the collision.
     Section 6's control removes the resolution and requires the disaster back:
     `sqlite3.IntegrityError` out of `init_retail()`, `user_version` stuck at
     15 -- the unbootable POS, measured rather than argued about.

  3. NEITHER DRAWER IS LOST. A session that has to be moved aside holds a float
     somebody physically counted into a till. It is never deleted, never
     silently closed, and never left open-with-a-NULL-terminal to dodge the
     constraint (which would keep `_open_cash_session_id` folding sales into
     it -- a pass condition that IS the bug signature). It is ENDED, with
     `ended_reason` naming why, `ended_by='System'` naming that no person did
     it, and `closing_float_counted`/`variance` left NULL because nobody
     counted it.

  4. THE OLD CONSTRAINT IS GENUINELY GONE, asserted behaviourally. Section 3
     opens two drawers on ONE branch from two different terminals and requires
     both to be accepted. Asserting that an index NAME has disappeared from
     `sqlite_master` would pass just as happily against a migration that
     dropped the name and left the constraint, or that created the new index
     without UNIQUE.

  5. THE CONSTRAINT ACTUALLY BINDS. Section 0's second guard inserts a genuine
     violation -- a second open session on a terminal that already has one --
     and requires SQLite to refuse it.

  6. v16 DOES NOT BOOBY-TRAP v17. `ensure_schema_version` re-enters the chain
     from the TOP whenever `user_version` is behind, so every future migration
     re-runs `_migrate_add_shift_cash_drawer` -- which creates the
     BRANCH-bound unique index. On a shop that has adopted the terminal-bound
     drawer, two open sessions on one branch are exactly what the feature is
     for, and recreating that index over them raises out of `init_retail()`.
     The next release would refuse to start on the shops using the feature
     most. `_v10_branch_index_is_superseded` is the guard; section 3 covers it
     and its control removes it and requires the crash back.

ANTI-VACUITY, STATED ON ITS OWN. Two of these tests exist only to prove the
others are testing something:

  - `test_the_fixture_really_has_the_colliding_shape_before_the_migration_runs`
    asserts, BEFORE any migration, that this device has a real terminal
    identity and that the fixture's open sessions genuinely map onto one
    (company_id, terminal_id) pair. If `local_terminal_id()` ever returns None
    here -- no local_device.json, a changed app-data layout -- every collision
    scenario in this file silently becomes a test of a migration with nothing
    to resolve, and passes.
  - `test_the_new_constraint_refuses_a_genuine_violation` asserts the finished
    index rejects the exact insert it exists to reject.

Self-contained bootstrap, matching every other file in this directory (no
shared conftest.py exists here): own temp app-data dir, own local device
record, real `init_retail()`. Run:

    pytest products/retail/tests/retail_v16_terminal_cash_drawer_test.py -v
"""
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import pytest

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_TMP_DIRS = []


def teardown_module(module):
    for d in _TMP_DIRS:
        shutil.rmtree(d, ignore_errors=True)
    _TMP_DIRS.clear()


# ── fixture bedrock ─────────────────────────────────────────────────────────

def _fresh_app_data(prefix):
    tmp = tempfile.mkdtemp(prefix=prefix)
    _TMP_DIRS.append(tmp)
    os.environ['AURA_APP_DATA'] = tmp
    return tmp


def _write_local_device(app_data, device_uuid=None):
    """Give this install a REAL local device identity, through the real file.

    `local_terminal_id()` is `peek_local_device_uuid()`, which reads
    <AURA_APP_DATA>/device/local_device.json and -- deliberately -- never
    creates it. So a test that wants a terminal id has to put one there.

    Writing the file rather than monkeypatching `local_terminal_id` is the
    whole point: the migration's backfill, `_stamp()`'s write-time value and
    `device_registry.devices`' key are supposed to be ONE identity, and a
    patched-out lookup would prove the migration works against a value that
    exists nowhere in the product.
    """
    device_uuid = device_uuid or str(uuid.uuid4())
    device_dir = os.path.join(app_data, 'device')
    os.makedirs(device_dir, exist_ok=True)
    with open(os.path.join(device_dir, 'local_device.json'), 'w', encoding='utf-8') as f:
        json.dump({'device_uuid': device_uuid}, f)
    return device_uuid


def _install(prefix='aura-retail-v16-', with_device=True):
    """A REAL install at the CURRENT version: fresh temp AURA_APP_DATA,
    optional local device record, the genuine `init_retail()` boot path, the
    full v0 -> v16 chain.

    Deliberately not a hand-built minimal schema. v16 runs after fifteen other
    steps, reads the shop clock out of `retail_settings`, writes `audit_log`
    and rewrites an index v10 created; a fixture supplying only `cash_sessions`
    would prove the step works somewhere this code never runs.

    Used by the tests that exercise the FINISHED constraint. The tests that
    exercise the UPGRADE use `_install_at_v15` below, because a database that
    already carries the new index cannot be made to hold the shape the upgrade
    has to survive.
    """
    tmp = _fresh_app_data(prefix)
    device_uuid = _write_local_device(tmp) if with_device else None

    import database.schema as sch
    sch.BASE_DIR = os.path.join(tmp, 'database')
    sch.SUBSYS_DIR = os.path.join(sch.BASE_DIR, 'subsystems')
    sch.init_retail()
    return sch, os.path.join(sch.SUBSYS_DIR, 'retail.db'), device_uuid


def _install_at_v15(prefix='aura-retail-v16-pre-', with_device=True):
    """A GENUINE schema-v15 retail.db: the real `init_retail()` with the v16
    step stubbed out and `RETAIL_SCHEMA_VERSION` pinned back to 15.

    THIS IS THE ONLY HONEST WAY TO FIXTURE THE UPGRADE, and the first draft of
    this file got it wrong in a way worth recording. Installing at v16 and then
    rewinding `PRAGMA user_version` to 15 -- which is what
    retail_v15_ledger_truth_migration_test.py does, correctly, for a migration
    that only ADDS rows -- cannot work here: the rewind moves a number, and the
    v16 index is still sitting on the table. Every attempt to insert the
    colliding shape the migration exists to resolve was refused by the very
    constraint under test, so the fixture could not build the state at all.

    Pinning the version and stubbing the step, the way the v13 and v14 test
    files already do for theirs, produces the real thing: the branch index in
    force, the terminal index absent, `ended_at`/`ended_by`/`ended_reason` not
    yet on the table. That is what the shop's database actually looks like the
    moment before it upgrades.
    """
    tmp = _fresh_app_data(prefix)
    device_uuid = _write_local_device(tmp) if with_device else None

    import database.schema as sch
    sch.BASE_DIR = os.path.join(tmp, 'database')
    sch.SUBSYS_DIR = os.path.join(sch.BASE_DIR, 'subsystems')

    real_version = sch.RETAIL_SCHEMA_VERSION
    real_v16 = sch._migrate_bind_cash_drawer_to_terminal
    sch.RETAIL_SCHEMA_VERSION = 15
    sch._migrate_bind_cash_drawer_to_terminal = lambda conn: None
    try:
        sch.init_retail()
    finally:
        sch.RETAIL_SCHEMA_VERSION = real_version
        sch._migrate_bind_cash_drawer_to_terminal = real_v16

    db_path = os.path.join(sch.SUBSYS_DIR, 'retail.db')
    conn = _open(db_path)
    try:
        assert conn.execute('PRAGMA user_version').fetchone()[0] == 15, (
            'the fixture did not actually produce a v15 database')
        assert sch._uid_index_shape(conn, 'cash_sessions', sch.V16_TERMINAL_INDEX) is None, (
            'the fixture already carries the constraint under test')
        assert sch._uid_index_shape(
            conn, 'cash_sessions', sch.V16_SUPERSEDED_BRANCH_INDEX) is not None, (
            'the fixture is missing the v10 branch constraint it is supposed to '
            'be upgrading away from')
        columns = {r[1] for r in conn.execute('PRAGMA table_info(cash_sessions)').fetchall()}
        assert not columns & {'ended_at', 'ended_by', 'ended_reason'}, (
            'the fixture already has v16\'s columns')
    finally:
        conn.close()
    return sch, db_path, device_uuid


def _today_at(second_of_day):
    """A timestamp guaranteed to fall on TODAY's business date, ordered by
    `second_of_day`.

    Anchored to local midnight rather than written as "N hours ago", and that
    is not a style choice. An offset from `datetime.now()` silently crosses
    midnight whenever the suite happens to run at night: a fixture meaning "two
    open drawers from today, which must collide" becomes "two drawers from
    yesterday, which the stale sweep ends before the collision pass ever sees
    them". The test still passes -- against a code path it was not written to
    cover -- and the collision resolution stops being tested at all after
    midnight. Measured, not imagined: it is how the first run of this file
    behaved.

    The shop in these fixtures declares no `business_day_start_hour`, so its
    business date IS the local calendar date (core/retail/metrics.py::
    business_day falls back to an unconfigured midnight boundary). Scenarios
    that declare one compute their own anchor -- see
    `test_the_business_date_is_the_shop_s_clock_not_the_calendar_s`.
    """
    midnight = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight + timedelta(seconds=second_of_day)


def _days_ago(days):
    """Local midnight `days` days back -- unambiguously an earlier business
    date whatever time the suite runs at."""
    midnight = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight - timedelta(days=days)


def _open(db_path):
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    return conn


def _rewind_to_v15(db_path):
    """Put the version marker back so the next boot re-runs the whole chain
    over a database that has ALREADY been through v16.

    This is the RETRY shape, not the upgrade shape, and the two are different
    fixtures on purpose -- see `_install_at_v15`. It is the state a real
    install reaches whenever a later step raises after v16 has done its work,
    and the state every future migration (v17 and on) will meet, since
    `ensure_schema_version` re-enters the chain from the top rather than from
    the step that is actually new.
    """
    conn = _open(db_path)
    try:
        conn.execute('PRAGMA user_version = 15')
        conn.commit()
    finally:
        conn.close()


def _user_version(db_path):
    conn = _open(db_path)
    try:
        return conn.execute('PRAGMA user_version').fetchone()[0]
    finally:
        conn.close()


def _new_company(conn, branch_count=1):
    """A second tenant in this database, with its own branches.

    The `company_id` is regenerated until it contains a non-digit.
    `retail_settings.company_id` is declared INTEGER, and SQLite's column
    affinity would coerce an all-digit TEXT key to a number on the way in --
    at which point the shop-clock lookup this file's business-date tests depend
    on would silently miss. A once-in-ten-million fixture flake is not worth
    the two lines it costs to make impossible.
    """
    company_id = uuid.uuid4().hex
    while company_id.isdigit():                 # pragma: no cover - see docstring
        company_id = uuid.uuid4().hex
    branch_ids = []
    for i in range(branch_count):
        cur = conn.execute(
            'INSERT INTO branches (company_id,name) VALUES (?,?)',
            (company_id, f'Branch {i + 1}'))
        branch_ids.append(cur.lastrowid)
    return company_id, branch_ids


def _ts(moment):
    """A timestamp in the shape api/retail_api.py::_now() writes."""
    return moment.strftime('%Y-%m-%d %H:%M:%S')


def _session(conn, company_id, branch_id, opened_at, opening_float,
             status='open', terminal_id=None, closed_at=None,
             counted=None, expected=None, variance=None, opened_by='u-1'):
    """One `cash_sessions` row, written the way the product writes them."""
    session_id = str(uuid.uuid4())
    conn.execute(
        'INSERT INTO cash_sessions '
        '(id,company_id,branch_id,opened_by,opened_at,opening_float,'
        ' closed_by,closed_at,closing_float_counted,closing_float_expected,'
        ' variance,status,terminal_id) '
        'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
        (session_id, company_id, branch_id, opened_by, _ts(opened_at),
         opening_float, 'u-2' if closed_at else None,
         _ts(closed_at) if closed_at else None,
         counted, expected, variance, status, terminal_id))
    return session_id


def _movement(conn, session_id, mtype, amount):
    conn.execute(
        'INSERT INTO cash_movements (id,session_id,type,amount,reason,created_by) '
        'VALUES (?,?,?,?,?,?)',
        (str(uuid.uuid4()), session_id, mtype, amount, 'fixture', 'u-1'))


def _declare_business_day(conn, company_id, start_hour):
    """Declare when this shop's trading day begins.

    `retail_settings` is created lazily by api/retail_api.py's
    `_ensure_credit_schema`, so a schema-only install does not have it -- which
    is itself the common case and is exercised by every other test here (the
    shop clock degrades to device local time). This helper covers the shop that
    HAS declared one.
    """
    conn.execute(
        'CREATE TABLE IF NOT EXISTS retail_settings ('
        ' company_id INTEGER, skey TEXT, svalue TEXT, PRIMARY KEY (company_id, skey))')
    conn.execute(
        'INSERT OR REPLACE INTO retail_settings (company_id,skey,svalue) VALUES (?,?,?)',
        (company_id, 'business_day_start_hour', str(start_hour)))


# ── reading the result ──────────────────────────────────────────────────────

MONEY_COLUMNS = ('opening_float', 'closing_float_counted',
                 'closing_float_expected', 'variance')


def _money_snapshot(db_path):
    """Every money figure on every session, keyed by session id.

    Deliberately every session in the database, not just the ones a scenario
    built: a migration that quietly rewrote a figure on some OTHER company's
    shift would be exactly as bad, and a snapshot scoped to the fixture would
    not see it.
    """
    conn = _open(db_path)
    try:
        return {
            row['id']: tuple(row[c] for c in MONEY_COLUMNS)
            for row in conn.execute(
                f"SELECT id,{','.join(MONEY_COLUMNS)} FROM cash_sessions").fetchall()
        }
    finally:
        conn.close()


def _movement_totals(db_path):
    conn = _open(db_path)
    try:
        return {row['id']: (row['type'], row['amount'])
                for row in conn.execute(
                    'SELECT id,type,amount FROM cash_movements').fetchall()}
    finally:
        conn.close()


def _sessions(db_path, company_id=None):
    conn = _open(db_path)
    try:
        if company_id is None:
            rows = conn.execute('SELECT * FROM cash_sessions').fetchall()
        else:
            rows = conn.execute(
                'SELECT * FROM cash_sessions WHERE company_id=?',
                (company_id,)).fetchall()
        return {row['id']: dict(row) for row in rows}
    finally:
        conn.close()


def _index_shape(db_path, name):
    conn = _open(db_path)
    try:
        import database.schema as sch
        return sch._uid_index_shape(conn, 'cash_sessions', name)
    finally:
        conn.close()


def _collisions(db_path):
    """The groups that would make `CREATE UNIQUE INDEX` raise, read through the
    migration's own probe so the fixture and the migration cannot disagree
    about what a collision is."""
    conn = _open(db_path)
    try:
        import database.schema as sch
        return [dict(r) for r in sch._v16_collision_probe(conn)]
    finally:
        conn.close()


def _force_end_audit(db_path):
    conn = _open(db_path)
    try:
        import database.schema as sch
        return [dict(r) for r in conn.execute(
            'SELECT * FROM audit_log WHERE action=? ORDER BY id',
            (sch.V16_FORCE_END_AUDIT_ACTION,)).fetchall()]
    finally:
        conn.close()


# ── 0. the anti-vacuity guards, stated on their own ─────────────────────────

def test_the_fixture_really_has_the_colliding_shape_before_the_migration_runs():
    """Two open drawers, one terminal, in a database that has NOT yet migrated.

    This is the guard every collision scenario below repeats inline, stated
    once on its own so that if the fixture ever stops producing a collision --
    a local device record that stops being read, an app-data layout change, a
    v13 writer that stops stamping `terminal_id` -- the failure names that
    cause directly instead of turning half this file into a test of a
    migration with nothing to do.

    It also pins the shape the collision has to have: the two sessions are on
    DIFFERENT branches, because the v10 index still in force at this point
    forbids two open drawers on one branch. That is precisely why the collision
    is invisible until v16 rebinds the key -- and why it is the ordinary
    multi-branch shop, not an exotic one, that would have met
    `CREATE UNIQUE INDEX` head on.
    """
    sch, db_path, device_uuid = _install_at_v15()

    assert device_uuid, 'the fixture did not write a local device record'
    assert sch.local_terminal_id() == device_uuid, (
        "local_terminal_id() cannot see this install's device record, so every "
        'collision scenario in this file would migrate with nothing to resolve')

    conn = _open(db_path)
    try:
        company_id, (branch_a, branch_b) = _new_company(conn, branch_count=2)
        first = _session(conn, company_id, branch_a, _today_at(1), 120.0,
                         terminal_id=device_uuid)
        second = _session(conn, company_id, branch_b, _today_at(2), 80.0,
                          terminal_id=device_uuid)
        conn.commit()
    finally:
        conn.close()

    collisions = _collisions(db_path)
    assert [(c['company_id'], c['terminal_id'], c['sessions']) for c in collisions] \
        == [(company_id, device_uuid, 2)], collisions
    assert first != second

    # And the constraint in force is still the v10 one, over the wrong key.
    branch_index = _index_shape(db_path, sch.V16_SUPERSEDED_BRANCH_INDEX)
    assert branch_index['columns'] == ['company_id', 'branch_id']
    assert _index_shape(db_path, sch.V16_TERMINAL_INDEX) is None


def test_the_new_constraint_refuses_a_genuine_violation():
    """The finished index rejects the exact insert it exists to reject.

    Asserted by writing a violating row and requiring SQLite to refuse it, not
    by reading the index's name or its declared SQL back out of
    `sqlite_master`. A name proves nothing: v13's F4 defect was a
    `CREATE UNIQUE INDEX IF NOT EXISTS` that found the name already taken by a
    PLAIN index, left it there, reported success and advanced the version with
    no uniqueness anywhere.
    """
    sch, db_path, device_uuid = _install()
    conn = _open(db_path)
    try:
        company_id, (branch_a, branch_b) = _new_company(conn, branch_count=2)
        _session(conn, company_id, branch_a, _today_at(1), 50.0,
                 terminal_id=device_uuid)
        conn.commit()

        with pytest.raises(sqlite3.IntegrityError):
            _session(conn, company_id, branch_b, _today_at(2), 60.0,
                     terminal_id=device_uuid)
        conn.rollback()

        # ...and the refusal really is about the TERMINAL, not a leftover
        # branch rule: the same second drawer, on the same second branch, from
        # a DIFFERENT terminal, is accepted.
        _session(conn, company_id, branch_b, _today_at(3), 60.0,
                 terminal_id=str(uuid.uuid4()))
        conn.commit()
    finally:
        conn.close()

    assert len(_sessions(db_path, company_id)) == 2


# ── 1. the two-year-old shop ────────────────────────────────────────────────

def _two_year_old_shop(conn, branches=3, months=24):
    """A shop that has been trading for two years: three branches, one
    counted-and-closed shift per branch per month, each with float in/out
    movements and a real variance.

    `terminal_id` is NULL on every one of them, which is what a shop trading
    since before v13 actually looks like -- the column did not exist when
    those drawers were counted.

    Figures are derived rather than random, so a failure names an exact number
    and two runs of this file build byte-identical shops.
    """
    company_id, branch_ids = _new_company(conn, branch_count=branches)
    start = _days_ago(months * 30)
    built = []
    for month in range(months):
        for index, branch_id in enumerate(branch_ids):
            opened = start + timedelta(days=month * 30, hours=8 + index)
            closed = opened + timedelta(hours=9)
            opening = 100.0 + 10 * index
            expected = opening + 250.0 + 37.5 * month
            counted = expected - (1.25 * ((month + index) % 5))
            session_id = _session(
                conn, company_id, branch_id, opened, opening,
                status='closed', terminal_id=None, closed_at=closed,
                counted=round(counted, 2), expected=round(expected, 2),
                variance=round(counted - expected, 2))
            _movement(conn, session_id, 'float_in', 20.0 + index)
            _movement(conn, session_id, 'float_out', 15.0 + month % 7)
            built.append(session_id)
    return company_id, branch_ids, built


def test_a_two_year_old_shop_migrates_with_every_session_s_money_unchanged():
    """The lesson from Phase 3, applied. A migration is not done when its own
    tests pass; it is done when a fixture built to look like a shop that has
    been running for two years goes through it and comes out with its money
    unchanged. v15 passed its own tests while turning shelves of 112/57/7 into
    -8/-3/-1 on exactly such a shop.
    """
    sch, db_path, device_uuid = _install_at_v15()
    conn = _open(db_path)
    try:
        company_id, branch_ids, built = _two_year_old_shop(conn)
        conn.commit()
    finally:
        conn.close()

    assert len(built) == 72, 'the fixture is not the two-year shop it claims to be'
    money_before = _money_snapshot(db_path)
    movements_before = _movement_totals(db_path)
    rows_before = len(_sessions(db_path))
    assert len(movements_before) == 144

    sch.init_retail()

    assert _user_version(db_path) == 16
    assert _money_snapshot(db_path) == money_before, (
        'v16 rewrote a money figure on a settled shift')
    assert _movement_totals(db_path) == movements_before, (
        'v16 touched the float in/out ledger')
    assert len(_sessions(db_path)) == rows_before, 'v16 lost or invented a session'
    assert not _force_end_audit(db_path), (
        'nothing was open, so nothing should have been force-ended')


def test_settled_shifts_keep_their_status_and_gain_the_counting_instant():
    """The ENDED / CLOSED split, applied to history that predates it.

    Under the pre-v16 model closing WAS counting -- there was no approval step
    to have skipped -- so `closed_at`/`closed_by` are restated into
    `ended_at`/`ended_by`: the same instant and the same person the row already
    carried, moved into the column the new code path reads. `status` is left
    alone. Rewriting years of settled shifts to 'ended' would be a
    non-additive DML pass over a shop's financial history to record the absence
    of an authority that did not exist when those drawers were counted, and
    would leave a permanent queue of shifts nobody will ever approve.
    """
    sch, db_path, device_uuid = _install_at_v15()
    conn = _open(db_path)
    try:
        company_id, branch_ids, built = _two_year_old_shop(conn, branches=1, months=3)
        conn.commit()
    finally:
        conn.close()

    before = _sessions(db_path, company_id)
    sch.init_retail()
    after = _sessions(db_path, company_id)

    for session_id in built:
        row, was = after[session_id], before[session_id]
        assert row['status'] == sch.CASH_SESSION_STATUS_CLOSED, (
            'a settled shift was restatused by the migration')
        assert row['ended_at'] == was['closed_at'], (
            'the counting instant was not restated onto ended_at')
        assert row['ended_by'] == was['closed_by']
        assert row['closed_at'] == was['closed_at'], 'the close trail was disturbed'
        assert row['closed_by'] == was['closed_by']
        assert row['ended_reason'] is None, (
            'a shift a person counted must not carry a force-end reason')


def test_closed_history_is_never_given_this_device_s_terminal_id():
    """v13 left `terminal_id` NULL on history because this device cannot prove
    it is the till that rang a sale from before the column existed. v16 does
    not overturn that -- a wrong terminal on a settled shift is worse than no
    terminal, because the row is the only surviving evidence about where that
    money was. Only an OPEN drawer is bound, and only because it is open HERE,
    NOW, with its float on this counter.
    """
    sch, db_path, device_uuid = _install_at_v15()
    conn = _open(db_path)
    try:
        company_id, branch_ids, built = _two_year_old_shop(conn, branches=2, months=2)
        live = _session(conn, company_id, branch_ids[0], _today_at(1), 90.0)
        conn.commit()
    finally:
        conn.close()

    sch.init_retail()

    after = _sessions(db_path, company_id)
    for session_id in built:
        assert after[session_id]['terminal_id'] is None, (
            'v16 stamped this device onto a shift it cannot prove it held')
    assert after[live]['terminal_id'] == device_uuid, (
        'the live drawer was not bound to this terminal')
    assert after[live]['status'] == sch.CASH_SESSION_STATUS_OPEN


# ── 2. open drawers, and the collision ──────────────────────────────────────

def test_an_install_with_one_open_session_per_branch_migrates():
    """Three branches, three open drawers, all opened today, all stamped with
    this device by v13's writers -- the exact shape that would have met
    `CREATE UNIQUE INDEX` head on."""
    sch, db_path, device_uuid = _install_at_v15()
    conn = _open(db_path)
    try:
        company_id, branch_ids = _new_company(conn, branch_count=3)
        opened = [
            _session(conn, company_id, branch_id, _today_at(10 + i), 100.0 + i,
                     terminal_id=device_uuid)
            for i, branch_id in enumerate(branch_ids)
        ]
        conn.commit()
    finally:
        conn.close()

    assert _collisions(db_path), 'fixture has no collision -- see the section 0 guard'
    money_before = _money_snapshot(db_path)

    sch.init_retail()

    assert _user_version(db_path) == 16
    assert _money_snapshot(db_path) == money_before
    after = _sessions(db_path, company_id)
    assert len(after) == 3, 'a drawer was deleted'
    still_open = [r for r in after.values() if r['status'] == sch.CASH_SESSION_STATUS_OPEN]
    assert len(still_open) == 1
    assert still_open[0]['id'] == opened[-1], 'the newest drawer did not keep the terminal'
    for row in after.values():
        if row['id'] != opened[-1]:
            assert row['ended_reason'] == sch.V16_ENDED_REASON_COLLISION


def test_two_open_sessions_that_would_collide_both_survive_the_migration():
    """Neither drawer is lost. One keeps the terminal and stays open; the other
    is ENDED, keeps its float, keeps its movements, and says on the row that
    nobody counted it."""
    sch, db_path, device_uuid = _install_at_v15()
    conn = _open(db_path)
    try:
        company_id, (branch_a, branch_b) = _new_company(conn, branch_count=2)
        older = _session(conn, company_id, branch_a, _today_at(1), 140.0,
                         terminal_id=device_uuid)
        newer = _session(conn, company_id, branch_b, _today_at(2), 75.0,
                         terminal_id=device_uuid)
        _movement(conn, older, 'paid_out', 12.5)
        conn.commit()
    finally:
        conn.close()

    assert _collisions(db_path), 'fixture has no collision -- see the section 0 guard'
    money_before = _money_snapshot(db_path)
    movements_before = _movement_totals(db_path)

    sch.init_retail()

    after = _sessions(db_path, company_id)
    assert set(after) == {older, newer}, 'a contested drawer was deleted'
    assert _money_snapshot(db_path) == money_before, (
        'a float was rewritten to make room for the constraint')
    assert _movement_totals(db_path) == movements_before

    assert after[newer]['status'] == sch.CASH_SESSION_STATUS_OPEN
    assert after[newer]['terminal_id'] == device_uuid
    assert after[older]['status'] == sch.CASH_SESSION_STATUS_ENDED
    assert after[older]['opening_float'] == 140.0
    assert not _collisions(db_path)


def test_the_shift_the_migration_ends_is_recorded_as_unverified_not_as_a_clean_close():
    """A shift nobody ended is a fact about the shop, not an error to hide.

    The row has to say all four things: it ended, the migration ended it,
    nobody counted it, and nobody accepted a variance. Two of those are said by
    what is PRESENT (`ended_reason`, `ended_by`) and two by what is deliberately
    ABSENT (`closing_float_counted`, `variance`) -- and `closed_at` staying
    NULL is what keeps it distinguishable from a shift that really did go
    through the close.
    """
    sch, db_path, device_uuid = _install_at_v15()
    conn = _open(db_path)
    try:
        company_id, (branch_a, branch_b) = _new_company(conn, branch_count=2)
        abandoned = _session(conn, company_id, branch_a, _today_at(1), 200.0,
                             terminal_id=device_uuid)
        _session(conn, company_id, branch_b, _today_at(2), 60.0,
                 terminal_id=device_uuid)
        conn.commit()
    finally:
        conn.close()

    sch.init_retail()

    row = _sessions(db_path, company_id)[abandoned]
    assert row['status'] == sch.CASH_SESSION_STATUS_ENDED
    assert row['ended_reason'] in sch.V16_UNVERIFIED_END_REASONS
    assert row['ended_by'] == sch.V16_ENDED_BY_SYSTEM
    assert row['ended_at'], 'the end instant was not recorded'
    assert row['closed_at'] is None, 'a force-end was laundered into a close'
    assert row['closed_by'] is None
    assert row['closing_float_counted'] is None, (
        'the migration claimed somebody counted this drawer')
    assert row['closing_float_expected'] is None
    assert row['variance'] is None, (
        'a variance figure is a claim that a count happened')
    assert row['opening_float'] == 200.0, 'the float somebody put in the till moved'

    # ...and the shop can find out where its drawer went.
    audit = _force_end_audit(db_path)
    assert [a['entity_id'] for a in audit] == [abandoned], audit
    assert audit[0]['user_id'] is None, 'the audit row names a person who did nothing'
    details = json.loads(audit[0]['details'])
    assert details['counted'] is False
    assert details['reason'] == row['ended_reason']
    assert details['opening_float'] == 200.0


def test_a_collision_created_by_the_backfill_itself_is_resolved():
    """The open drawers here carry NO terminal id -- they predate v13, which is
    what every session on a shop trading since before that migration looks
    like. The collision does not exist until v16's own backfill stamps both
    rows with this device. A resolution pass that ran BEFORE the backfill would
    find nothing to do and hand the index two colliding rows.
    """
    sch, db_path, device_uuid = _install_at_v15()
    conn = _open(db_path)
    try:
        company_id, (branch_a, branch_b) = _new_company(conn, branch_count=2)
        older = _session(conn, company_id, branch_a, _today_at(1), 55.0)
        newer = _session(conn, company_id, branch_b, _today_at(2), 65.0)
        conn.commit()
    finally:
        conn.close()

    # The anti-vacuity half that matters for THIS scenario: there is no
    # collision yet, so a migration that never backfilled would sail through
    # and this test would prove nothing about the resolution.
    assert not _collisions(db_path), (
        'the fixture already collides, so it is not the pre-v13 shape it claims')
    before = _sessions(db_path, company_id)
    assert before[older]['terminal_id'] is None
    assert before[newer]['terminal_id'] is None

    sch.init_retail()

    after = _sessions(db_path, company_id)
    assert after[newer]['status'] == sch.CASH_SESSION_STATUS_OPEN
    assert after[newer]['terminal_id'] == device_uuid
    assert after[older]['status'] == sch.CASH_SESSION_STATUS_ENDED
    assert after[older]['ended_reason'] == sch.V16_ENDED_REASON_COLLISION
    assert not _collisions(db_path)


def test_a_blank_terminal_id_is_normalised_rather_than_allowed_to_collide():
    """SQLite treats every NULL in a unique index as distinct from every other
    NULL, but treats two empty strings as EQUAL. So a blank `terminal_id`
    collides where a NULL one does not -- a difference that is invisible in
    review and fatal at boot. v16 normalises blanks on open rows to NULL before
    it groups anything.
    """
    sch, db_path, device_uuid = _install_at_v15(with_device=False)
    assert sch.local_terminal_id() is None, (
        'this scenario needs an install that cannot name its terminal, so the '
        'blanks are not simply overwritten by the backfill')

    conn = _open(db_path)
    try:
        company_id, (branch_a, branch_b) = _new_company(conn, branch_count=2)
        first = _session(conn, company_id, branch_a, _today_at(1), 30.0,
                         terminal_id='')
        second = _session(conn, company_id, branch_b, _today_at(2), 40.0,
                          terminal_id='')
        conn.commit()
    finally:
        conn.close()

    # ANTI-VACUITY, and stated against SQLite rather than against the
    # migration's own probe: building the v16 index on this data RIGHT NOW is
    # refused. Two identical empty strings are a duplicate where two NULLs are
    # not, which is the entire hazard -- and asserting it this way means the
    # test still fails if the normalisation is ever deleted, instead of
    # quietly agreeing with a probe that had been taught to look away.
    conn = _open(db_path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                'CREATE UNIQUE INDEX v16_blank_probe ON cash_sessions'
                "(company_id, terminal_id) WHERE status='open'")
    finally:
        conn.close()

    sch.init_retail()

    after = _sessions(db_path, company_id)
    assert after[first]['terminal_id'] is None
    assert after[second]['terminal_id'] is None
    # Both drawers survive AND both stay open: a blank was never a terminal, so
    # normalising it takes nothing away from the shop.
    assert after[first]['status'] == sch.CASH_SESSION_STATUS_OPEN
    assert after[second]['status'] == sch.CASH_SESSION_STATUS_OPEN


# ── the shop clock ──────────────────────────────────────────────────────────

def test_a_shift_that_ran_past_its_business_date_is_ended():
    sch, db_path, device_uuid = _install_at_v15()
    conn = _open(db_path)
    try:
        company_id, (branch_id,) = _new_company(conn)
        forgotten = _session(conn, company_id, branch_id, _days_ago(40), 175.0,
                             terminal_id=device_uuid)
        conn.commit()
    finally:
        conn.close()

    # No collision at all here -- one open drawer, one terminal. The stale
    # sweep is the only thing that can act on it, so this scenario is not
    # quietly being carried by the collision pass.
    assert not _collisions(db_path)

    sch.init_retail()

    row = _sessions(db_path, company_id)[forgotten]
    assert row['status'] == sch.CASH_SESSION_STATUS_ENDED
    assert row['ended_reason'] == sch.V16_ENDED_REASON_STALE
    assert row['opening_float'] == 175.0
    assert row['variance'] is None


def test_a_drawer_opened_today_is_left_trading():
    """The sweep must not end the shift the shop is standing at. Without this,
    the guard's pass condition would be "every drawer is closed", which is the
    same as having no drawer feature at all."""
    sch, db_path, device_uuid = _install_at_v15()
    conn = _open(db_path)
    try:
        company_id, (branch_id,) = _new_company(conn)
        live = _session(conn, company_id, branch_id, _today_at(1), 45.0,
                        terminal_id=device_uuid)
        conn.commit()
    finally:
        conn.close()

    sch.init_retail()

    row = _sessions(db_path, company_id)[live]
    assert row['status'] == sch.CASH_SESSION_STATUS_OPEN
    assert row['ended_at'] is None
    assert row['ended_reason'] is None
    assert not _force_end_audit(db_path)


def test_the_business_date_is_the_shop_s_clock_not_the_calendar_s():
    """A shop whose trading day starts at 04:00 measures staleness against ITS
    OWN trading-day boundaries, not the calendar's midnight -- and, since
    FIX B (the stale-sweep threshold below), only a drawer that has crossed
    TWO of those boundaries is stale enough to force-end. A drawer that
    crossed exactly one -- carried a couple of minutes past this shop's 04:00
    open -- is exactly the shift a cashier is still standing at, and must
    stay open. See the "shifts that ran past their business date" comment in
    `_migrate_bind_cash_drawer_to_terminal` step 3 for why the bar is "before
    the PREVIOUS business date", not "before today's".

    Written as a TRIO, each on its OWN terminal, so the assertions cannot pass
    for the wrong reason. Three terminals rather than letting the backfill
    hand two of them the same device id: this test is about the STALE SWEEP
    (step 3), which runs before the terminal backfill (step 4), and a shared
    terminal would pull step 5's collision resolution into the result too,
    which is a different mechanism this file already covers elsewhere.

      * `inside`        -- one minute after this trading day opened: must
                           stay OPEN (comfortably current).
      * `crossed_once`  -- one minute before this trading day opened, i.e.
                           it carried over ONE boundary: must now stay OPEN
                           too -- this is the case FIX B exists for.
      * `crossed_twice` -- one minute before the trading day BEFORE that,
                           i.e. TWO boundaries back: must still be ENDED.

    All three timestamps are computed from the exact instant this shop's
    trading day began, not guessed at from an "N hours ago" offset, which
    would land on the wrong side of a boundary depending on what time the
    suite happens to run.
    """
    sch, db_path, device_uuid = _install_at_v15()
    start_hour = 4
    now = datetime.now()
    day_began = (now - timedelta(hours=start_hour)).replace(
        hour=0, minute=0, second=0, microsecond=0) + timedelta(hours=start_hour)

    conn = _open(db_path)
    try:
        company_id, (branch_a, branch_b, branch_c) = _new_company(conn, branch_count=3)
        _declare_business_day(conn, company_id, start_hour=start_hour)
        inside = _session(conn, company_id, branch_a,
                          day_began + timedelta(minutes=1), 85.0,
                          terminal_id=str(uuid.uuid4()))
        crossed_once = _session(conn, company_id, branch_b,
                                day_began - timedelta(minutes=1), 95.0,
                                terminal_id=str(uuid.uuid4()))
        crossed_twice = _session(conn, company_id, branch_c,
                                 day_began - timedelta(days=1, minutes=1), 65.0,
                                 terminal_id=str(uuid.uuid4()))
        conn.commit()
    finally:
        conn.close()

    sch.init_retail()

    after = _sessions(db_path, company_id)
    assert after[crossed_twice]['status'] == sch.CASH_SESSION_STATUS_ENDED, (
        'the sweep never fired, so the rest of this test proves nothing')
    assert after[crossed_twice]['ended_reason'] == sch.V16_ENDED_REASON_STALE
    assert after[crossed_once]['status'] == sch.CASH_SESSION_STATUS_OPEN, (
        'a drawer that merely carried past ONE trading-day boundary was '
        'force-ended -- see FIX B: the bar is "before the PREVIOUS business '
        'date", not "before today\'s"')
    assert after[crossed_once]['ended_reason'] is None
    assert after[inside]['status'] == sch.CASH_SESSION_STATUS_OPEN, (
        "a shift still inside the shop's trading day was ended")


def test_a_drawer_opened_just_before_midnight_is_left_trading():
    """FIX B's literal field reproduction. A genuine v15 install with no
    `retail_settings` row (so the shop clock falls back to plain local
    midnight), a drawer opened at 23:59, carried across the calendar boundary,
    still open, opening_float 420.00, cashier still serving. Under the OLD
    comparison (`opened < today`) that drawer is force-ended and permanently
    marked `unverified_stale_business_date` the instant `init_retail()` next
    runs; under the fixed one it keeps trading.

    ANCHORED TO LOCAL MIDNIGHT, NOT TO `datetime.now()`, and this is a
    correction with a measured reason rather than a style preference. An
    earlier version of this test opened the drawer at `datetime.now() -
    timedelta(hours=2)` and its docstring claimed "running this at any hour
    still exercises the fix". It does not, and the claim was checked the only
    way a claim like that can be: by reverting `_migrate_bind_cash_drawer_to_
    terminal` step 3 to `opened < today` and re-running. This test PASSED
    against the reverted code -- because outside the 00:00-02:00 window
    "two hours ago" is the SAME calendar date as now, so the drawer never
    crossed a boundary at all and neither comparison could ever end it. For
    twenty-two hours of the day the test was asserting an outcome that both
    the fixed and the broken code produce: the pass condition proved nothing.
    (`test_the_business_date_is_the_shop_s_clock_not_the_calendar_s` did fail
    against the revert, so FIX B was never unguarded -- but this test, which
    exists specifically to cover the UNCONFIGURED shop-clock fallback that
    trio does not use, was carrying none of it.)

    `_today_at(0) - 1 minute` is 23:59 on the previous calendar date at
    whatever hour the suite runs, so the drawer has crossed exactly ONE
    boundary every time. Only a run that crosses midnight between building
    this fixture and `init_retail()` reading the clock -- a sub-second race at
    exactly 00:00 -- could disturb it, which is the same residual the rest of
    this file's midnight-anchored fixtures carry.
    """
    sch, db_path, device_uuid = _install_at_v15()
    conn = _open(db_path)
    try:
        company_id, (branch_id,) = _new_company(conn)
        live = _session(conn, company_id, branch_id,
                        _today_at(0) - timedelta(minutes=1), 420.0,
                        terminal_id=device_uuid)
        conn.commit()
    finally:
        conn.close()

    sch.init_retail()

    row = _sessions(db_path, company_id)[live]
    assert row['status'] == sch.CASH_SESSION_STATUS_OPEN, (
        'a drawer opened at 23:59 and still being served was force-ended '
        'merely for having carried across a calendar midnight -- see FIX B: '
        'the bar is "before the PREVIOUS business date", not "before today\'s"')
    assert row['ended_reason'] is None
    assert row['ended_at'] is None
    assert row['opening_float'] == 420.0


def test_a_drawer_opened_three_days_ago_is_still_ended():
    """The other side of FIX B's boundary: raising the bar from "before
    today" to "before the previous business date" must not turn the stale
    sweep off. Three days is unambiguously past even the new threshold.
    """
    sch, db_path, device_uuid = _install_at_v15()
    conn = _open(db_path)
    try:
        company_id, (branch_id,) = _new_company(conn)
        forgotten = _session(conn, company_id, branch_id, _days_ago(3), 300.0,
                             terminal_id=device_uuid)
        conn.commit()
    finally:
        conn.close()

    sch.init_retail()

    row = _sessions(db_path, company_id)[forgotten]
    assert row['status'] == sch.CASH_SESSION_STATUS_ENDED
    assert row['ended_reason'] == sch.V16_ENDED_REASON_STALE


# ── 3. the old constraint is genuinely gone ─────────────────────────────────

def test_two_drawers_on_one_branch_are_legal_when_they_are_different_terminals():
    """The whole point of the phase, asserted as behaviour.

    A desktop and a phone at the same counter each open their own drawer. Under
    v10's index the second INSERT was refused outright; after v16 it must be
    accepted, and the two sessions must stay independent.

    Deliberately NOT `assert 'idx_cash_sessions_one_open_per_branch' not in
    sqlite_master`. That would pass just as happily against a migration that
    dropped the name and left an equivalent constraint behind, or one that
    renamed the old index, or one that created the new index without UNIQUE.
    """
    sch, db_path, device_uuid = _install()
    conn = _open(db_path)
    try:
        company_id, (branch_id,) = _new_company(conn)
        desktop = _session(conn, company_id, branch_id, _today_at(1), 100.0,
                           terminal_id=device_uuid)
        phone = _session(conn, company_id, branch_id, _today_at(2), 50.0,
                         terminal_id=str(uuid.uuid4()))
        conn.commit()
    finally:
        conn.close()

    after = _sessions(db_path, company_id)
    assert {after[desktop]['status'], after[phone]['status']} == {
        sch.CASH_SESSION_STATUS_OPEN}
    assert after[desktop]['branch_id'] == after[phone]['branch_id']
    assert after[desktop]['terminal_id'] != after[phone]['terminal_id']


def test_a_later_chain_re_entry_does_not_put_the_branch_constraint_back():
    """The landmine v16 lays for v17, defused.

    `ensure_schema_version` re-enters `_migrate_retail_schema` from the TOP
    whenever `user_version` is behind, so every future migration re-runs
    `_migrate_add_shift_cash_drawer` -- which creates the branch-bound unique
    index. On a shop that has adopted the terminal-bound drawer, two open
    sessions on one branch are legal and expected, and recreating that index
    over them raises `UNIQUE constraint failed` out of `init_retail()`, which
    `app.py::init_app()` calls with no handler. The next release would refuse
    to start on precisely the shops using the feature most.
    """
    sch, db_path, device_uuid = _install()
    conn = _open(db_path)
    try:
        company_id, (branch_id,) = _new_company(conn)
        desktop = _session(conn, company_id, branch_id, _today_at(1), 100.0,
                           terminal_id=device_uuid)
        phone = _session(conn, company_id, branch_id, _today_at(2), 50.0,
                         terminal_id=str(uuid.uuid4()))
        conn.commit()
    finally:
        conn.close()

    _rewind_to_v15(db_path)
    sch.init_retail()

    assert _user_version(db_path) == 16
    after = _sessions(db_path, company_id)
    assert after[desktop]['status'] == sch.CASH_SESSION_STATUS_OPEN
    assert after[phone]['status'] == sch.CASH_SESSION_STATUS_OPEN
    assert _index_shape(db_path, sch.V16_SUPERSEDED_BRANCH_INDEX) is None


def test_without_the_supersession_guard_the_next_launch_cannot_boot():
    """The control for the test above: restore the unconditional
    `CREATE UNIQUE INDEX IF NOT EXISTS idx_cash_sessions_one_open_per_branch`
    and require the boot crash to reappear.

    A guard nobody has watched fail is a guard nobody knows the shape of. If
    this control ever stops reproducing, the guard beside it is no longer what
    is keeping a v17 release bootable.
    """
    sch, db_path, device_uuid = _install()
    conn = _open(db_path)
    try:
        company_id, (branch_id,) = _new_company(conn)
        _session(conn, company_id, branch_id, _today_at(1), 100.0,
                 terminal_id=device_uuid)
        _session(conn, company_id, branch_id, _today_at(2), 50.0,
                 terminal_id=str(uuid.uuid4()))
        conn.commit()
    finally:
        conn.close()

    _rewind_to_v15(db_path)
    real_guard = sch._v10_branch_index_is_superseded
    sch._v10_branch_index_is_superseded = lambda conn_: False
    try:
        with pytest.raises(sqlite3.IntegrityError):
            sch.init_retail()
    finally:
        sch._v10_branch_index_is_superseded = real_guard

    assert _user_version(db_path) == 15

    # ...and with the guard restored, the same database migrates.
    sch.init_retail()
    assert _user_version(db_path) == 16


def test_the_same_terminal_in_two_companies_is_two_drawers():
    """The constraint is (company_id, terminal_id), not terminal_id. One
    install can host more than one company, and one physical till serving both
    of them is two drawers, not one."""
    sch, db_path, device_uuid = _install()
    conn = _open(db_path)
    try:
        first_company, (first_branch,) = _new_company(conn)
        second_company, (second_branch,) = _new_company(conn)
        _session(conn, first_company, first_branch, _today_at(1), 10.0,
                 terminal_id=device_uuid)
        _session(conn, second_company, second_branch, _today_at(2), 20.0,
                 terminal_id=device_uuid)
        conn.commit()
    finally:
        conn.close()

    assert len(_sessions(db_path, first_company)) == 1
    assert len(_sessions(db_path, second_company)) == 1


# ── 4. the install that cannot name its terminal ────────────────────────────

def test_an_install_with_no_device_record_keeps_its_drawer_and_gets_the_constraint():
    """`local_terminal_id()` is `peek_local_device_uuid()`, which returns None
    when this install has never generated one -- a stripped Android build, or a
    machine whose identity file has not been written yet -- and deliberately
    never CREATES it.

    Such an install keeps its open drawer. Ending a shop's live till because
    this build cannot name its own terminal would be destroying real
    bookkeeping to enforce a constraint that has nothing to say about it. The
    index is still created, and still binds every drawer that DOES have a
    terminal.
    """
    sch, db_path, device_uuid = _install_at_v15(with_device=False)
    assert device_uuid is None
    assert sch.local_terminal_id() is None, (
        'this install has a device record after all -- the scenario is not the '
        'unnameable-terminal case it claims to be')

    conn = _open(db_path)
    try:
        company_id, (branch_id,) = _new_company(conn)
        live = _session(conn, company_id, branch_id, _today_at(1), 33.0)
        conn.commit()
    finally:
        conn.close()

    sch.init_retail()

    assert _user_version(db_path) == 16
    row = _sessions(db_path, company_id)[live]
    assert row['status'] == sch.CASH_SESSION_STATUS_OPEN, (
        'a live drawer was ended because this build could not name its terminal')
    assert row['terminal_id'] is None

    # The constraint exists and binds anything that IS nameable.
    assert sch._v16_terminal_index_is_correct(
        _index_shape(db_path, sch.V16_TERMINAL_INDEX))
    conn = _open(db_path)
    try:
        named = str(uuid.uuid4())
        _session(conn, company_id, branch_id, _today_at(2), 5.0, terminal_id=named)
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            _session(conn, company_id, branch_id, _today_at(3), 6.0, terminal_id=named)
        conn.rollback()
    finally:
        conn.close()


# ── 5. idempotence and resumption ───────────────────────────────────────────

def test_running_the_migration_again_changes_nothing():
    sch, db_path, device_uuid = _install_at_v15()
    conn = _open(db_path)
    try:
        company_id, branch_ids, built = _two_year_old_shop(conn, branches=2, months=2)
        _session(conn, company_id, branch_ids[0], _today_at(1), 70.0,
                 terminal_id=device_uuid)
        _session(conn, company_id, branch_ids[1], _today_at(2), 80.0,
                 terminal_id=device_uuid)
        conn.commit()
    finally:
        conn.close()

    sch.init_retail()

    first_pass = _sessions(db_path)
    audit_after_first = _force_end_audit(db_path)
    assert audit_after_first, 'the first pass did nothing, so a second pass proves nothing'

    _rewind_to_v15(db_path)
    sch.init_retail()

    assert _user_version(db_path) == 16
    assert _sessions(db_path) == first_pass, 'the second pass moved something'
    assert _force_end_audit(db_path) == audit_after_first, (
        'the second pass re-ended a shift that was already ended')


def test_an_interrupted_migration_is_finished_by_the_next_launch():
    """A migration that dies partway must leave a database the next launch can
    finish, not one it has to be rescued from.

    The interruption is injected at the collision probe -- after the columns
    have been added and after the sweep and the backfill have written -- which
    is the deepest point at which failing still leaves the old index in force
    and the new one absent. `ensure_schema_version` does not advance
    `user_version` on a raise, so the next launch re-enters the whole chain
    from the top and every step re-runs against whatever survived.
    """
    sch, db_path, device_uuid = _install_at_v15()
    conn = _open(db_path)
    try:
        company_id, (branch_a, branch_b) = _new_company(conn, branch_count=2)
        older = _session(conn, company_id, branch_a, _today_at(1), 210.0,
                         terminal_id=device_uuid)
        newer = _session(conn, company_id, branch_b, _today_at(2), 115.0,
                         terminal_id=device_uuid)
        conn.commit()
    finally:
        conn.close()

    money_before = _money_snapshot(db_path)
    real_probe = sch._v16_collision_probe

    def _die(conn_):
        raise RuntimeError('power cut, halfway through v16')

    sch._v16_collision_probe = _die
    try:
        with pytest.raises(RuntimeError):
            sch.init_retail()
    finally:
        sch._v16_collision_probe = real_probe

    # Halfway: the marker did not move, the old constraint is still the one in
    # force, the new one was never built, and not a figure of the shop's money
    # was lost on the way.
    assert _user_version(db_path) == 15
    assert _index_shape(db_path, sch.V16_TERMINAL_INDEX) is None
    assert _index_shape(db_path, sch.V16_SUPERSEDED_BRANCH_INDEX) is not None
    assert _money_snapshot(db_path) == money_before
    assert len(_sessions(db_path, company_id)) == 2

    # The next launch finishes it.
    sch.init_retail()

    assert _user_version(db_path) == 16
    assert sch._v16_terminal_index_is_correct(
        _index_shape(db_path, sch.V16_TERMINAL_INDEX))
    assert _index_shape(db_path, sch.V16_SUPERSEDED_BRANCH_INDEX) is None
    assert _money_snapshot(db_path) == money_before
    after = _sessions(db_path, company_id)
    assert after[newer]['status'] == sch.CASH_SESSION_STATUS_OPEN
    assert after[older]['status'] == sch.CASH_SESSION_STATUS_ENDED


# ── 6. the controls: what the resolution is actually holding up ─────────────

def test_without_the_resolution_the_index_creation_is_what_fails():
    """The disaster, reproduced.

    Every guard in this file is worth exactly what it prevents, so the
    prevention is measured rather than argued about. With the force-end
    disabled AND the probe blinded, the two open drawers reach
    `CREATE UNIQUE INDEX` -- and SQLite refuses, out of `init_retail()`, which
    `app.py::init_app()` calls unconditionally with no handler. That is a POS
    that will not start, and the message an operator would be left with names
    no shop, no shift and no recovery.

    If this control ever stops reproducing, the resolution beside it is no
    longer what is keeping the app bootable and the tests beside it have
    quietly become tests of nothing.
    """
    sch, db_path, device_uuid = _install_at_v15()
    conn = _open(db_path)
    try:
        company_id, (branch_a, branch_b) = _new_company(conn, branch_count=2)
        _session(conn, company_id, branch_a, _today_at(1), 60.0,
                 terminal_id=device_uuid)
        _session(conn, company_id, branch_b, _today_at(2), 70.0,
                 terminal_id=device_uuid)
        conn.commit()
    finally:
        conn.close()

    assert _collisions(db_path), 'fixture has no collision -- see the section 0 guard'

    real_force_end = sch._v16_force_end
    real_probe = sch._v16_collision_probe
    sch._v16_force_end = lambda *a, **k: None
    sch._v16_collision_probe = lambda conn_: []
    try:
        with pytest.raises(sqlite3.IntegrityError):
            sch.init_retail()
    finally:
        sch._v16_force_end = real_force_end
        sch._v16_collision_probe = real_probe

    assert _user_version(db_path) == 15, (
        'the version advanced past a constraint that was never built')
    assert _index_shape(db_path, sch.V16_TERMINAL_INDEX) is None

    # ...and with the resolution restored, the same install migrates.
    sch.init_retail()
    assert _user_version(db_path) == 16
    assert not _collisions(db_path)


def test_the_backstop_refuses_with_a_message_instead_of_letting_sqlite_refuse():
    """With only the force-end disabled, the probe catches the collision and
    refuses before the index is attempted.

    The value of the backstop is not that it prevents anything the resolution
    does not already prevent -- it is unreachable by construction -- but that
    the refusal it produces names the company, the terminal, the count and the
    recovery, where SQLite's names a table and two column names. An operator
    who reaches either one cannot open the app to go looking.
    """
    sch, db_path, device_uuid = _install_at_v15()
    conn = _open(db_path)
    try:
        company_id, (branch_a, branch_b) = _new_company(conn, branch_count=2)
        _session(conn, company_id, branch_a, _today_at(1), 25.0,
                 terminal_id=device_uuid)
        _session(conn, company_id, branch_b, _today_at(2), 35.0,
                 terminal_id=device_uuid)
        conn.commit()
    finally:
        conn.close()

    real_force_end = sch._v16_force_end
    sch._v16_force_end = lambda *a, **k: None
    try:
        with pytest.raises(sch.RetailCashDrawerBindError) as caught:
            sch.init_retail()
    finally:
        sch._v16_force_end = real_force_end

    message = str(caught.value)
    assert company_id in message, 'the refusal does not name the shop'
    assert device_uuid in message, 'the refusal does not name the terminal'
    assert 'ended' in message, 'the refusal does not name a recovery'
    assert _user_version(db_path) == 15


def test_a_cash_sessions_table_with_no_terminal_column_is_declined_not_repaired():
    """v16 binds to `terminal_id`, which belongs to v13 and is added
    unconditionally by it, so a `cash_sessions` without that column means v13
    has not run against this database at all.

    The right answer is to decline. An earlier draft self-healed with an
    `ALTER TABLE ... ADD COLUMN terminal_id`, which broke a real test (v13 was
    left with nothing to add, and
    retail_v13_additive_only_behavioural_test.py's exact-columns assertion is
    what caught it) and was wrong for a bigger reason than that: a table with
    no terminal column has WRITERS that know nothing about terminals either, so
    the new index would constrain a column nothing ever populates -- binding
    nothing -- while the branch-bound index it replaced was dropped. Replacing
    a working constraint with a decorative one is worse than leaving the
    working one in place.

    Asserted as a unit test against a hand-built table, and honest about being
    one: the state is unreachable through the real chain, which is precisely
    why it needs pinning here rather than being left to a reader to re-derive.
    """
    sch, db_path, device_uuid = _install()
    conn = _open(db_path)
    try:
        # A pre-v13 `cash_sessions`: schema v10's shape exactly, no actor
        # triple on it at all.
        conn.execute('DROP TABLE cash_sessions')
        conn.execute("""
            CREATE TABLE cash_sessions (
                id TEXT PRIMARY KEY,
                company_id TEXT,
                branch_id INTEGER NOT NULL,
                opened_by TEXT,
                opened_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                opening_float REAL NOT NULL DEFAULT 0,
                closed_by TEXT,
                closed_at TIMESTAMP,
                closing_float_counted REAL,
                closing_float_expected REAL,
                variance REAL,
                status TEXT NOT NULL DEFAULT 'open'
            )
        """)
        conn.execute(
            'CREATE UNIQUE INDEX idx_cash_sessions_one_open_per_branch '
            "ON cash_sessions(company_id, branch_id) WHERE status='open'")
        conn.commit()

        result = sch._migrate_bind_cash_drawer_to_terminal(conn)
        conn.commit()

        assert 'skipped' in result, result
        columns = {r[1] for r in conn.execute(
            'PRAGMA table_info(cash_sessions)').fetchall()}
        assert 'terminal_id' not in columns, (
            'v16 added a column that belongs to v13')
        assert not columns & {'ended_at', 'ended_by', 'ended_reason'}, (
            'v16 declined to bind but wrote to the table anyway')
        # The table is never left unconstrained: the branch-bound index it
        # would have replaced is still standing.
        assert sch._uid_index_shape(
            conn, 'cash_sessions', sch.V16_SUPERSEDED_BRANCH_INDEX) is not None
        assert sch._uid_index_shape(
            conn, 'cash_sessions', sch.V16_TERMINAL_INDEX) is None
    finally:
        conn.close()


def test_two_open_drawers_with_a_null_company_id_are_left_alone():
    """SQL uniqueness treats a NULL as distinct from everything, including
    another NULL, and that rule applies to EVERY column of the index key --
    not just to `terminal_id`.

    So two open sessions with a NULL `company_id` and the identical terminal id
    are not a duplicate as far as the index is concerned, and ending one of
    them would be taking a live till off a shop to satisfy a constraint that
    was never going to object. `cash_sessions.company_id` is nullable and
    always has been, so this is a row a legacy install can genuinely be
    holding, and a `GROUP BY company_id, terminal_id` written without the
    matching `IS NOT NULL` groups them together and reports one.

    The anti-vacuity half is asserted against SQLite rather than against the
    migration: the index really does accept both rows afterwards, which is the
    fact the resolution pass has to agree with.
    """
    sch, db_path, device_uuid = _install_at_v15()
    conn = _open(db_path)
    try:
        _, (branch_a, branch_b) = _new_company(conn, branch_count=2)
        first = _session(conn, None, branch_a, _today_at(1), 15.0,
                         terminal_id=device_uuid)
        second = _session(conn, None, branch_b, _today_at(2), 25.0,
                          terminal_id=device_uuid)
        conn.commit()
    finally:
        conn.close()

    sch.init_retail()

    assert _user_version(db_path) == 16
    after = _sessions(db_path)
    assert after[first]['status'] == sch.CASH_SESSION_STATUS_OPEN, (
        'a drawer the constraint would have accepted was ended anyway')
    assert after[second]['status'] == sch.CASH_SESSION_STATUS_OPEN
    assert after[first]['opening_float'] == 15.0
    assert after[second]['opening_float'] == 25.0


# ── 7. the boot-before-login gap (FIX A) ─────────────────────────────────────
#
# `local_terminal_id()` is `peek_local_device_uuid()`, which by design NEVER
# creates `local_device.json`. The only production writer is
# `device_context.resolve_local_device()`, reached only via
# `GET /api/devices/me`, which the frontend calls from `app-shell.js::init()`
# -- AFTER LOGIN. `init_retail()` runs at PROCESS BOOT, before any login. So
# on the FIRST boot after an install upgrades to v16, the migration's own
# backfill (step 4) runs with no device identity, every open drawer's
# `terminal_id` stays NULL, and `user_version` advances to 16 regardless --
# correctly, because the migration did everything it could with the identity
# it had. The device identity then appears moments later, at login, and
# `_migrate_bind_cash_drawer_to_terminal` NEVER RUNS AGAIN (`user_version` is
# not behind anymore). Without `_v16_rebind_orphaned_open_drawers`, that
# shop's live, counted-into drawer is stranded with a NULL terminal forever.

def test_a_drawer_stranded_with_no_terminal_gets_bound_once_the_device_identity_appears():
    """The exact sequence a real boot follows: migrate with no identity yet,
    then the identity appears (login happened), then boot again. The
    stranded drawer must become bindable -- and, to prove the binding is
    real rather than decorative, a second drawer for the same terminal must
    now be refused by the real constraint.
    """
    sch, db_path, device_uuid = _install_at_v15(with_device=False)
    assert sch.local_terminal_id() is None

    conn = _open(db_path)
    try:
        company_id, (branch_id,) = _new_company(conn)
        live = _session(conn, company_id, branch_id, _today_at(1), 50.0)
        conn.commit()
    finally:
        conn.close()

    sch.init_retail()   # migrates 15 -> 16 with no device identity at all

    stranded = _sessions(db_path, company_id)[live]
    assert stranded['status'] == sch.CASH_SESSION_STATUS_OPEN
    assert stranded['terminal_id'] is None, (
        'the fixture is not the stranded-drawer case it claims to be')

    # The device identity appears -- this is what a real login does.
    device_uuid = _write_local_device(os.environ['AURA_APP_DATA'])
    assert sch.local_terminal_id() == device_uuid

    sch.init_retail()   # no version to advance; the rebind is what must act

    after = _sessions(db_path, company_id)[live]
    assert after['status'] == sch.CASH_SESSION_STATUS_OPEN
    assert after['terminal_id'] == device_uuid, (
        'the stranded drawer was never bound once the device identity '
        'appeared -- see FIX A')

    # And the binding is REAL: a second open drawer for the same terminal is
    # refused by the partial unique index, not merely by a flag on a row.
    conn = _open(db_path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            _session(conn, company_id, branch_id, _today_at(2), 10.0,
                     terminal_id=device_uuid)
        conn.rollback()
    finally:
        conn.close()


def test_the_rebind_skips_rather_than_fails_when_this_terminal_already_has_an_open_drawer():
    """FIX A rule 3: binding is SKIPPED, not attempted-and-caught, when this
    terminal already holds an open drawer for the same company -- because
    binding would violate `idx_cash_sessions_one_open_per_terminal`, and a
    boot-time reconciliation raising `IntegrityError` out of `init_retail()`
    (which `app.py::init_app()` calls with no handler) would turn "help this
    drawer find its terminal" into "the POS will not start".
    """
    sch, db_path, device_uuid = _install_at_v15(with_device=False)
    conn = _open(db_path)
    try:
        company_id, (branch_a, branch_b) = _new_company(conn, branch_count=2)
        orphan = _session(conn, company_id, branch_a, _today_at(1), 40.0)
        conn.commit()
    finally:
        conn.close()

    sch.init_retail()   # migrates with no identity -- orphan stays NULL

    device_uuid = _write_local_device(os.environ['AURA_APP_DATA'])

    # This terminal already has a genuine open drawer on this company.
    conn = _open(db_path)
    try:
        already_bound = _session(conn, company_id, branch_b, _today_at(2), 60.0,
                                 terminal_id=device_uuid)
        conn.commit()
    finally:
        conn.close()

    sch.init_retail()   # must not raise, and must not bind the orphan

    after = _sessions(db_path, company_id)
    assert after[orphan]['terminal_id'] is None, (
        'the rebind bound a drawer that would collide with an already-open '
        'one on the same terminal')
    assert after[orphan]['status'] == sch.CASH_SESSION_STATUS_OPEN, (
        'skipping the bind must not end the drawer -- it is still a live '
        'till, just not yet nameable')
    assert after[already_bound]['terminal_id'] == device_uuid
    assert after[already_bound]['status'] == sch.CASH_SESSION_STATUS_OPEN


def test_two_stranded_drawers_on_one_company_bind_one_and_leave_the_other():
    """FIX A rule 3, in the shape the rule's docstring says is the reason the
    check has to be re-run per orphan rather than taken as a snapshot before
    the loop: TWO open drawers on one company, BOTH stranded with a NULL
    terminal by the same identity-less migration.

    Nothing in this file covered that case, and it is the one where a
    plausible-looking implementation breaks. Compute the "does this terminal
    already hold an open drawer for this company?" answer ONCE, before the
    loop, and it is False for both rows -- so both get bound to the same
    terminal, and the second UPDATE meets
    `idx_cash_sessions_one_open_per_terminal` and raises `IntegrityError`
    straight out of `init_retail()`, which `app.py::init_app()` calls with no
    handler. That is the POS-will-not-start failure the whole rebind exists
    to stay off, reached BY the code meant to prevent it.

    The correct outcome is not "both bound" -- that is what the index forbids
    -- but "exactly one bound, the other left NULL and still OPEN". The
    unbound one is a real drawer holding real money; it stays a live till
    that simply has no terminal name yet, exactly as when this device could
    not name itself at all.
    """
    sch, db_path, device_uuid = _install_at_v15(with_device=False)
    assert sch.local_terminal_id() is None

    conn = _open(db_path)
    try:
        company_id, (branch_a, branch_b) = _new_company(conn, branch_count=2)
        first = _session(conn, company_id, branch_a, _today_at(1), 11.0)
        second = _session(conn, company_id, branch_b, _today_at(2), 22.0)
        conn.commit()
    finally:
        conn.close()

    sch.init_retail()   # migrates with no identity: BOTH strand at NULL

    stranded = _sessions(db_path, company_id)
    assert stranded[first]['terminal_id'] is None
    assert stranded[second]['terminal_id'] is None, (
        'the fixture is not the two-stranded-drawers case it claims to be')

    device_uuid = _write_local_device(os.environ['AURA_APP_DATA'])

    sch.init_retail()   # must not raise on the SECOND orphan

    after = _sessions(db_path, company_id)
    bound = [sid for sid in (first, second)
             if after[sid]['terminal_id'] == device_uuid]
    assert len(bound) == 1, (
        f'expected exactly one of the two stranded drawers to be bound to '
        f'this terminal, got {len(bound)} -- see FIX A rule 3: the '
        f'collision check has to be re-run for every orphan, because binding '
        f'the first one is what makes the second one a collision')
    unbound = [sid for sid in (first, second) if sid not in bound]
    assert after[unbound[0]]['terminal_id'] is None
    assert after[unbound[0]]['status'] == sch.CASH_SESSION_STATUS_OPEN, (
        'the drawer that could not be bound was ended -- it is a live till '
        'holding real money, not a row to tidy away')
    assert after[bound[0]]['status'] == sch.CASH_SESSION_STATUS_OPEN
    assert {after[first]['opening_float'], after[second]['opening_float']} == {11.0, 22.0}
    assert _user_version(db_path) == 16


def test_an_install_with_no_device_identity_never_wedges_across_repeated_boots():
    """The no-wedge property, held down across THREE separate boots with no
    device identity ever appearing -- a stripped Android build, or a machine
    that has genuinely never logged in. `_v16_rebind_orphaned_open_drawers`
    must return immediately every single time, and the live drawer must
    still be exactly where it was: open, unbound, untouched.
    """
    sch, db_path, device_uuid = _install_at_v15(with_device=False)
    assert sch.local_terminal_id() is None

    conn = _open(db_path)
    try:
        company_id, (branch_id,) = _new_company(conn)
        live = _session(conn, company_id, branch_id, _today_at(1), 20.0)
        conn.commit()
    finally:
        conn.close()

    for _cycle in range(3):
        sch.init_retail()
        assert _user_version(db_path) == 16
        row = _sessions(db_path, company_id)[live]
        assert row['status'] == sch.CASH_SESSION_STATUS_OPEN, (
            f'the drawer was disturbed on repeated boot {_cycle}')
        assert row['terminal_id'] is None
        assert row['opening_float'] == 20.0


# ── 8. the blank-terminal definition has to be ONE definition (FIX C) ───────

@pytest.mark.parametrize('blank_value', ['', '   ', '\t', '\t\t', '\xa0', '\n'])
def test_every_flavour_of_blank_terminal_id_is_normalised_without_wedging(blank_value):
    """REPRODUCED: step 4 used to blank with SQL `TRIM(terminal_id) = ''`,
    which strips only 0x20, while step 5 used to skip with Python
    `not str(value).strip()`, which strips every code point `str.isspace()`
    calls whitespace. '' and '   ' agreed under both; '\\t', '\\t\\t', '\\xa0'
    and '\\n' did not -- so two open drawers sharing one of THOSE values were
    a genuine SQLite duplicate that the old step 5 called "unnameable" and
    left unresolved, and `CREATE UNIQUE INDEX` raised out of `init_retail()`
    with `user_version` stuck at 15, forever.

    Parametrized over every value CLAUDE.md's own reproduction named, each
    run building TWO open drawers that share the identical value: the probe,
    the resolution and SQLite must now all agree that this is a "blank" (so
    both survive, both open, both normalised to NULL) rather than a
    duplicate the migration silently fails to clear.
    """
    sch, db_path, device_uuid = _install_at_v15(with_device=False)
    assert sch.local_terminal_id() is None, (
        'this scenario needs an install that cannot name its terminal, so '
        'the blanks are not simply overwritten by the backfill')

    conn = _open(db_path)
    try:
        company_id, (branch_a, branch_b) = _new_company(conn, branch_count=2)
        first = _session(conn, company_id, branch_a, _today_at(1), 30.0,
                         terminal_id=blank_value)
        second = _session(conn, company_id, branch_b, _today_at(2), 40.0,
                          terminal_id=blank_value)
        conn.commit()
    finally:
        conn.close()

    sch.init_retail()   # must not raise, and must not wedge user_version

    assert _user_version(db_path) == 16
    after = _sessions(db_path, company_id)
    assert after[first]['terminal_id'] is None, (
        f'{blank_value!r} was not normalised to NULL')
    assert after[second]['terminal_id'] is None
    assert after[first]['status'] == sch.CASH_SESSION_STATUS_OPEN, (
        f'{blank_value!r} was treated as a genuine terminal name and one of '
        f'the two drawers was force-ended over it')
    assert after[second]['status'] == sch.CASH_SESSION_STATUS_OPEN


def test_padded_and_bare_values_stay_different_terminals():
    """The other half of FIX C's requirement: fixing the blank definition
    must not turn INTO over-grouping. '  X  ' and 'X' are different strings
    to SQLite's exact byte-for-byte comparison, and neither one is blank
    under `str.strip()` (each strips down to a non-empty 'X'), so they must
    stay two different terminals -- both drawers open, neither one collided
    with or normalised into the other.
    """
    sch, db_path, device_uuid = _install_at_v15(with_device=False)
    conn = _open(db_path)
    try:
        company_id, (branch_a, branch_b) = _new_company(conn, branch_count=2)
        padded = _session(conn, company_id, branch_a, _today_at(1), 12.0,
                          terminal_id='  X  ')
        bare = _session(conn, company_id, branch_b, _today_at(2), 18.0,
                        terminal_id='X')
        conn.commit()
    finally:
        conn.close()

    sch.init_retail()

    assert _user_version(db_path) == 16
    after = _sessions(db_path, company_id)
    assert after[padded]['terminal_id'] == '  X  ', (
        'a non-blank value was rewritten by the blank normalisation')
    assert after[bare]['terminal_id'] == 'X'
    assert after[padded]['status'] == sch.CASH_SESSION_STATUS_OPEN, (
        '"  X  " and "X" were treated as the same terminal and one drawer '
        'was force-ended over a collision SQLite was never going to raise'
    )
    assert after[bare]['status'] == sch.CASH_SESSION_STATUS_OPEN


# ── 9. an index name is database-global (FIX D) ──────────────────────────────

def test_an_index_name_collision_on_a_different_table_is_refused_by_name_not_by_sqlite():
    """REPRODUCED: a plain `CREATE INDEX idx_cash_sessions_one_open_per_terminal
    ON sales(company_id)` on a real v15 database. `_uid_index_shape` only asks
    `PRAGMA index_list("cash_sessions")`, which correctly says the name is not
    THERE, so the bare `CREATE UNIQUE INDEX` a few lines later raises
    `OperationalError: index ... already exists` straight out of
    `init_retail()` -- naming no shop, no shift and no recovery, on exactly
    the path `RetailCashDrawerBindError` exists to cover.
    """
    sch, db_path, device_uuid = _install_at_v15()
    conn = _open(db_path)
    try:
        conn.execute(f'CREATE INDEX {sch.V16_TERMINAL_INDEX} ON sales(company_id)')
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(sch.RetailCashDrawerBindError) as caught:
        sch.init_retail()

    message = str(caught.value)
    assert 'sales' in message, 'the refusal does not name the conflicting table'
    assert sch.V16_TERMINAL_INDEX in message
    assert _user_version(db_path) == 15, (
        'the version advanced past an index that was never actually built '
        'on cash_sessions')

    # ...and once the conflicting index is out of the way, the same install
    # migrates normally.
    conn = _open(db_path)
    try:
        conn.execute(f'DROP INDEX {sch.V16_TERMINAL_INDEX}')
        conn.commit()
    finally:
        conn.close()
    sch.init_retail()
    assert _user_version(db_path) == 16


# ── 10. create-before-drop is not decorative (FIX E) ─────────────────────────

def test_an_interruption_between_the_two_index_statements_never_leaves_neither_constraint():
    """REPRODUCED: swapping the CREATE and the DROP passed all 24 tests that
    existed before this one, because nothing exercised the ordering directly
    -- `test_an_interrupted_migration_is_finished_by_the_next_launch` injects
    its failure at `_v16_collision_probe`, upstream of BOTH statements, so it
    asserts an outcome at a point where the ordering cannot matter.

    THIS TEST DELIBERATELY BYPASSES `init_retail()`'s OWN ROLLBACK-ON-
    EXCEPTION SAFETY NET, and that is not an oversight -- it is the whole
    point. `_migrate_bind_cash_drawer_to_terminal` is called directly on an
    already-open connection, and the interrupt handler explicitly
    `conn.commit()`s right before raising. That models the ACTUAL hazard
    the CREATE-before-DROP ordering exists to guard against: a real power
    cut (or any future refactor that changes how durability works here)
    commits whatever has already run the instant it runs, without waiting
    for the rest of this function to finish. init_retail()'s rollback is a
    SEPARATE, real safety net that also protects a clean Python exception
    inside the whole migration chain (see
    `test_an_interrupted_migration_is_finished_by_the_next_launch` for that
    one) -- but it is not what step 6's own CREATE-before-DROP ordering is
    defending, and a test that goes through it cannot tell the two orderings
    apart, because a full-transaction rollback restores the pre-migration
    branch index either way regardless of which DDL statement ran first.
    Committing explicitly at the interruption point is what isolates the
    property THIS ordering specifically guarantees.

    Order-agnostic about WHICH of the two statements is "first" in the code
    -- it raises on the SECOND `_v16_run_index_ddl` call, whichever
    statement that turns out to be -- which is what lets the mutation proof
    below swap the two lines and re-run this exact test unmodified.

    MUTATION PROOF (recorded in this repo's task log verbatim): with the
    CREATE and DROP calls in `_migrate_bind_cash_drawer_to_terminal` step 6
    swapped, this test FAILS at the "left with neither" assertion --
    `AssertionError: cash_sessions was left with NEITHER...` -- because the
    swapped order lets the DROP commit for real first (removing the branch
    constraint durably) and then the commit-and-raise fires on the CREATE
    (so the terminal constraint never gets built and never commits either):
    `cash_sessions` is left with neither index in force.
    """
    sch, db_path, device_uuid = _install_at_v15()
    conn = _open(db_path)
    try:
        company_id, (branch_id,) = _new_company(conn)
        _session(conn, company_id, branch_id, _today_at(1), 40.0,
                 terminal_id=device_uuid)
        conn.commit()

        real_ddl = sch._v16_run_index_ddl
        calls = []

        def _commit_then_die_on_second_statement(conn_, sql):
            calls.append(sql)
            if len(calls) == 2:
                # The modeled power cut: whatever ran before this point is
                # forced durable RIGHT NOW, and the process dies before the
                # second statement executes.
                conn_.commit()
                raise RuntimeError('power cut, between the two index statements')
            return real_ddl(conn_, sql)

        sch._v16_run_index_ddl = _commit_then_die_on_second_statement
        try:
            with pytest.raises(RuntimeError):
                sch._migrate_bind_cash_drawer_to_terminal(conn)
        finally:
            sch._v16_run_index_ddl = real_ddl

        assert len(calls) == 2, (
            'the interrupt did not land between the two index statements -- '
            'this control is not testing what it claims to')

        terminal_shape = sch._uid_index_shape(conn, 'cash_sessions', sch.V16_TERMINAL_INDEX)
        branch_shape = sch._uid_index_shape(conn, 'cash_sessions', sch.V16_SUPERSEDED_BRANCH_INDEX)
        assert terminal_shape is not None or branch_shape is not None, (
            'cash_sessions was left with NEITHER the branch nor the terminal '
            'constraint after the interruption -- a power cut here would let '
            'two open drawers share one branch with nothing in the database '
            'to stop it. See FIX E: this assertion is what fails if the '
            'CREATE and DROP statements are ever swapped.')
    finally:
        conn.close()

    # The next launch finishes the job regardless of which statement ran.
    assert _user_version(db_path) == 15, (
        'the raw migration call must not have touched PRAGMA user_version -- '
        'only ensure_schema_version does that, and this test bypassed it')
    sch.init_retail()
    assert _user_version(db_path) == 16
    assert sch._v16_terminal_index_is_correct(
        _index_shape(db_path, sch.V16_TERMINAL_INDEX))
    assert _index_shape(db_path, sch.V16_SUPERSEDED_BRANCH_INDEX) is None
