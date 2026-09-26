"""Aura Retail -- schema v41 (one set of books across devices, wave 1: GL
journal sync) migration coverage. Builder B's assigned test file
(BOOKS-WAVE1-PLAN.md, section 6, "Builder B").

WHAT V41 CLAIMS AT THE SCHEMA LAYER, AND THEREFORE WHAT THIS FILE HAS TO
PROVE (database/schema.py::_migrate_add_gl_journal_sync and the W1-0 fix
inside _migrate_add_general_ledger). The sync EMIT side (core/retail/
general_ledger.py) and APPLY side (commercial_runtime/sync/sync_
service.py) are each a SEPARATE builder's own file and own test file --
this file proves only what database/schema.py itself is responsible for:

  1. `RETAIL_SCHEMA_VERSION` is 41.
  2. `gl_journals` carries `uid`/`source_uid`, the partial unique index on
     `uid`, and its two supporting indexes.
  3. `uid`/`source_uid` are WRITE-ONCE, not append-only-by-value: NULL ->
     a real value is allowed (the shape the boot-time legacy backfill
     needs), but a set value can never change again, including back to
     NULL -- both directions mutation-proved, both columns.
  4. The append-only trigger's re-widening SURVIVES a second chain
     replay. `_migrate_add_general_ledger` (v38's own step, earlier in the
     same chain) unconditionally narrows the SAME trigger back down on
     EVERY replay (R7 in the plan) -- this migration's re-widening must
     therefore also be unconditional, or a later, unrelated version bump
     would silently lose the uid/source_uid guard.
  5. W1-0: a LATENT, PRE-EXISTING bug in v38's own opening-balance
     backfill -- it double-, triple-, quadruple-posts the same opening
     balance on every later chain replay, because nothing before this fix
     ever checked whether it had already run. Proved RED against the
     pre-fix code (quoted verbatim in this wave's build report) before
     the `gl_already_existed` guard landed.
  6. A real v37->v41 upgrade taken in one boot posts exactly one opening-
     balance journal, that journal ends up with a real `uid`, and exactly
     one `sync_outbox` row is queued for it.
  7. The pre-existing cursor-reset behaviour (`_migrate_add_cheques`'s own
     `UPDATE sync_cursor SET relay_url = NULL`) still fires on a v41
     upgrade -- v41 deliberately adds no reset of its own (this wave's own
     plan, section 5: "Cursor reset: v41 does not add its own").
  8. The boot-time legacy-uid backfill is idempotent across two boots.

Follows the SAME bootstrap convention as retail_gl_schema_test.py and
retail_gl_append_only_test.py (no shared conftest.py exists for products/
retail/tests/): its own temp app-data dir per test via `_install()`, real
`init_retail()`, no Flask app anywhere in this file.

CRITICAL: exactly ONE pytest process per file (AUDIT-010) -- these test
files pollute each other in a shared pytest process; see products/
run_all_tests.py. This wave's own plan additionally forbids running this
suite (or any other) through run_all_tests.py or against the live lab.

Run:
    pytest products/retail/tests/retail_v41_gl_sync_migration_test.py -q
"""
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import time
from decimal import Decimal
from pathlib import Path

import pytest

PRODUCT_DIR = Path(__file__).resolve().parents[1]        # products/retail
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent                    # aura-fullsuits
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_TMP_DIRS = []


def teardown_module(module):
    for d in _TMP_DIRS:
        shutil.rmtree(d, ignore_errors=True)
    _TMP_DIRS.clear()


def _fresh_app_data(prefix):
    tmp = tempfile.mkdtemp(prefix=prefix)
    _TMP_DIRS.append(tmp)
    os.environ['AURA_APP_DATA'] = tmp
    return tmp


def _install():
    """A REAL install: fresh temp AURA_APP_DATA, the genuine `init_retail()`
    boot path, the full v0 -> current migration chain. Matches
    retail_gl_schema_test.py / retail_gl_append_only_test.py's own
    identical helper -- deliberately not a hand-built minimal schema, same
    reasoning both of those files already give."""
    tmp = _fresh_app_data('aura-retail-v41-glsync-')
    import database.schema as sch
    sch.BASE_DIR = os.path.join(tmp, 'database')
    sch.SUBSYS_DIR = os.path.join(sch.BASE_DIR, 'subsystems')
    sch.init_retail()
    return sch, os.path.join(sch.SUBSYS_DIR, 'retail.db')


def _open(db_path):
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    return conn


def _user_version(db_path):
    conn = _open(db_path)
    try:
        return conn.execute('PRAGMA user_version').fetchone()[0]
    finally:
        conn.close()


def _table_columns(conn, table):
    """Column names from LIVE PRAGMA table_info, never a hand-typed list --
    same ratchet convention retail_gl_append_only_test.py's own
    `_table_columns` already established."""
    return [row[1] for row in conn.execute(f'PRAGMA table_info({table})').fetchall()]


def _trigger_sql(conn, name):
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='trigger' AND name=?", (name,)
    ).fetchone()
    return row[0] if row else None


def _seed_legacy_journal(conn, gl, cid, memo='seed'):
    """One balanced journal in the PRE-v41 shape: a RAW INSERT naming only
    the v38 columns, so `uid` and `source_uid` are genuinely NULL.

    CORRECTED 2026-09-26 (wave verifier). This helper used to post through
    `gl.post_journal` on the stated assumption that `post_journal` "does not
    write a `uid`". Builder A's half of this wave made that false:
    `post_journal` now mints a uid on every journal, so the write-once test
    below failed at its own precondition and could no longer exercise the
    NULL -> value direction at all -- and the source_uid test only still
    passed because no `sales` row with id 1 existed for `post_journal` to
    derive a `source_uid` from. A raw INSERT is the only way to make a
    genuinely legacy row (BOOKS-WAVE1-PLAN.md section 6, trap 5: never make
    "legacy" journals with the new code). INSERT is allowed by the
    append-only triggers; only UPDATE/DELETE are refused."""
    gl.ensure_default_chart(conn, cid)
    cash_id = conn.execute(
        "SELECT id FROM gl_accounts WHERE company_id=? AND code=?", (cid, gl.ACCOUNT_CASH)
    ).fetchone()[0]
    rev_id = conn.execute(
        "SELECT id FROM gl_accounts WHERE company_id=? AND code=?", (cid, gl.ACCOUNT_SALES_REVENUE)
    ).fetchone()[0]
    cur = conn.execute(
        "INSERT INTO gl_journals (company_id, branch_id, business_date, reference, memo, "
        "source_type, source_id, currency, reversed_journal_id, posted_by) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (cid, None, '2026-09-21', None, memo, 'sale', 1, 'JOD', None, 'System'),
    )
    jid = cur.lastrowid
    conn.execute(
        "INSERT INTO gl_journal_lines (journal_id, account_id, debit, credit, memo) VALUES (?,?,?,?,?)",
        (jid, cash_id, 5.0, 0.0, memo),
    )
    conn.execute(
        "INSERT INTO gl_journal_lines (journal_id, account_id, debit, credit, memo) VALUES (?,?,?,?,?)",
        (jid, rev_id, 0.0, 5.0, memo),
    )
    conn.commit()
    return jid


def _rewind_to_pre_v38(conn):
    """Simulate a pre-v38 install: drop the GL tables/triggers a fresh
    install already created, so the next `init_retail()` boot takes the
    REAL v37->current upgrade path, including the opening-balance
    backfill. Matches retail_gl_schema_test.py's own `test_opening_
    balance_backfill_fires_on_upgrade_and_ties_to_subledger` verbatim."""
    conn.executescript("""
        DROP TRIGGER IF EXISTS trg_gl_journals_no_update;
        DROP TRIGGER IF EXISTS trg_gl_journals_no_delete;
        DROP TRIGGER IF EXISTS trg_gl_journal_lines_no_update;
        DROP TRIGGER IF EXISTS trg_gl_journal_lines_no_delete;
        DROP TABLE IF EXISTS gl_journal_lines;
        DROP TABLE IF EXISTS gl_journals;
        DROP TABLE IF EXISTS gl_accounts;
    """)
    conn.execute("PRAGMA user_version = 37")
    conn.commit()


def _force_second_chain_replay(sch, db_path):
    """Simulate a LATER version bump replaying the WHOLE
    `_migrate_retail_schema` chain a second time against an
    ALREADY-migrated database -- the exact mechanism a real v41->v42 (or
    any future bump) hits, without needing a second real version to
    exist. `ensure_schema_version` gates the entire chain purely on
    `user_version < target_version` (migration_safety.py) -- it has no
    notion of "which steps already ran" -- so rewinding just the STORED
    MARKER, with every table already in place, is a faithful proxy for
    that real mechanism, not a shortcut around it. One level up from
    `_rewind_to_pre_v38` above: that one rewinds to BEFORE gl_journals
    exists (a real UPGRADE); this one rewinds to AFTER it already exists
    (a real REPLAY)."""
    conn = _open(db_path)
    conn.execute(f'PRAGMA user_version = {sch.RETAIL_SCHEMA_VERSION - 1}')
    conn.commit()
    conn.close()
    sch.init_retail()


# ── 1-2. Schema shape ───────────────────────────────────────────────────

def test_retail_schema_version_is_41():
    sch, db_path = _install()
    assert sch.RETAIL_SCHEMA_VERSION >= 41, (
        "never == 41 -- see retail_v17_catalogue_migration_test.py's own "
        ">= convention, so a later wave bumping this constant further "
        "does not have to come back and edit this assertion"
    )
    assert _user_version(db_path) == sch.RETAIL_SCHEMA_VERSION


def test_gl_journals_has_uid_source_uid_and_partial_unique_index():
    sch, db_path = _install()
    conn = _open(db_path)
    try:
        cols = _table_columns(conn, 'gl_journals')
        assert 'uid' in cols, f"gl_journals is missing uid -- columns: {cols}"
        assert 'source_uid' in cols, f"gl_journals is missing source_uid -- columns: {cols}"

        idx_rows = conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='index' AND tbl_name='gl_journals'"
        ).fetchall()
        by_name = {r['name']: (r['sql'] or '') for r in idx_rows}

        assert 'idx_gl_journals_uid' in by_name, f"indexes present: {sorted(by_name)}"
        assert 'UNIQUE' in by_name['idx_gl_journals_uid'].upper(), by_name['idx_gl_journals_uid']
        assert 'UID IS NOT NULL' in by_name['idx_gl_journals_uid'].upper(), (
            f"idx_gl_journals_uid must be a PARTIAL unique index (WHERE uid "
            f"IS NOT NULL), so the many legacy NULL rows never collide with "
            f"each other. SQL: {by_name['idx_gl_journals_uid']}"
        )

        assert 'idx_gl_journals_uid_pending' in by_name, f"indexes present: {sorted(by_name)}"
        assert 'UID IS NULL' in by_name['idx_gl_journals_uid_pending'].upper(), (
            by_name['idx_gl_journals_uid_pending']
        )

        assert 'idx_gl_journals_source_uid' in by_name, f"indexes present: {sorted(by_name)}"
    finally:
        conn.close()


# ── 3. uid / source_uid write-once, both directions ─────────────────────

def test_uid_is_write_once():
    """NULL -> value is allowed (the boot backfill's own shape); value ->
    a DIFFERENT value and value -> NULL are both refused. Mutation-proved
    in an isolated copy of the tree: M-B1 (drop the `uid` clause from the
    trigger's WHEN) turns BOTH refusal assertions below green wrongly --
    see this wave's own build report for the RED/GREEN quotes."""
    sch, db_path = _install()
    conn = _open(db_path)
    try:
        from core.retail import general_ledger as gl
        jid = _seed_legacy_journal(conn, gl, 1)

        row = conn.execute("SELECT uid FROM gl_journals WHERE id=?", (jid,)).fetchone()
        assert row['uid'] is None, (
            "fixture bug: the seeded legacy row already carries a uid, so the "
            "NULL -> value direction below would prove nothing"
        )

        # NULL -> value: allowed.
        conn.execute("UPDATE gl_journals SET uid='legacy-uid-1' WHERE id=?", (jid,))
        conn.commit()
        row = conn.execute("SELECT uid FROM gl_journals WHERE id=?", (jid,)).fetchone()
        assert row['uid'] == 'legacy-uid-1'

        # value -> a DIFFERENT value: refused.
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE gl_journals SET uid='legacy-uid-2' WHERE id=?", (jid,))
        row = conn.execute("SELECT uid FROM gl_journals WHERE id=?", (jid,)).fetchone()
        assert row['uid'] == 'legacy-uid-1', 'a blocked UPDATE must not have changed the value anyway'

        # value -> NULL: refused.
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE gl_journals SET uid=NULL WHERE id=?", (jid,))
        row = conn.execute("SELECT uid FROM gl_journals WHERE id=?", (jid,)).fetchone()
        assert row['uid'] == 'legacy-uid-1'
    finally:
        conn.close()


def test_source_uid_is_write_once():
    """Identical shape to test_uid_is_write_once, for the sibling column --
    the two clauses in the trigger's WHEN are independent OR terms, so a
    mutation that only removes the uid clause (M-B1) would leave THIS
    column's guard intact, and vice versa; each needs its own proof."""
    sch, db_path = _install()
    conn = _open(db_path)
    try:
        from core.retail import general_ledger as gl
        jid = _seed_legacy_journal(conn, gl, 1)
        assert conn.execute(
            "SELECT source_uid FROM gl_journals WHERE id=?", (jid,)
        ).fetchone()['source_uid'] is None, (
            "fixture bug: the seeded legacy row already carries a source_uid"
        )

        conn.execute("UPDATE gl_journals SET source_uid='sale-uid-1' WHERE id=?", (jid,))
        conn.commit()
        row = conn.execute("SELECT source_uid FROM gl_journals WHERE id=?", (jid,)).fetchone()
        assert row['source_uid'] == 'sale-uid-1'

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE gl_journals SET source_uid='sale-uid-2' WHERE id=?", (jid,))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE gl_journals SET source_uid=NULL WHERE id=?", (jid,))

        row = conn.execute("SELECT source_uid FROM gl_journals WHERE id=?", (jid,)).fetchone()
        assert row['source_uid'] == 'sale-uid-1', 'a blocked UPDATE must not have changed the value anyway'
    finally:
        conn.close()


# ── 4. The trigger re-widening survives a second chain replay (R7) ──────

def test_trigger_is_rewidened_after_a_second_chain_run():
    """R7 in the plan: v38's own step (`_migrate_add_general_ledger`)
    unconditionally DROPs and (re)CREATEs the SAME trigger name on EVERY
    chain replay, using SQL that only knows about the original 11 v38
    columns. If THIS migration's own re-widening were not ALSO
    unconditional (see `_migrate_add_gl_journal_sync`'s own docstring,
    "THE TRIGGER IS UNCONDITIONALLY DROPPED AND RECREATED"), a second
    replay would leave the NARROW trigger in force -- v38's step runs
    first in the same chain call and would win via `CREATE TRIGGER IF NOT
    EXISTS` finding a same-named trigger already there. Forces exactly
    that second replay via `_force_second_chain_replay` and reads the
    trigger back out of `sqlite_master`, not just checking it once after
    a fresh install -- a fresh install alone cannot distinguish "always
    wide" from "wide only the first time". Mutation-proved: M-B2 (`CREATE
    TRIGGER IF NOT EXISTS` without the preceding `DROP TRIGGER`) turns
    this RED -- see this wave's own build report for the quote."""
    sch, db_path = _install()
    _force_second_chain_replay(sch, db_path)

    conn = _open(db_path)
    try:
        sql = _trigger_sql(conn, 'trg_gl_journals_no_update')
        assert sql is not None, 'trg_gl_journals_no_update does not exist after a second chain replay'
        assert re.search(r'\bOLD\.uid\b', sql), (
            f"trg_gl_journals_no_update lost its uid write-once clause after "
            f"a second chain replay -- v38's own step narrowed it back down "
            f"and nothing re-widened it afterwards. Trigger SQL:\n{sql}"
        )
        assert re.search(r'\bOLD\.source_uid\b', sql), (
            f"trg_gl_journals_no_update lost its source_uid write-once "
            f"clause after a second chain replay. Trigger SQL:\n{sql}"
        )
    finally:
        conn.close()


# ── 5. W1-0: the opening-balance backfill is not actually idempotent ────

def test_a_later_upgrade_does_not_repost_the_opening_balance():
    """W1-0 (see `_migrate_add_general_ledger`'s own corrected docstring,
    "THE OPENING-BALANCE BACKFILL WAS NOT ACTUALLY IDEMPOTENT", for the
    full story). Reproduces the REAL, two-step upgrade path rather than
    calling the private chain function directly: seed a nonzero AR/AP
    balance on a pre-v38 (v37) database, let a real `init_retail()` boot
    take it all the way to the current version (posting exactly one
    opening-balance journal -- the correct, first-ever backfill), then
    force a SECOND chain replay (`_force_second_chain_replay`) -- the
    exact mechanism a later, unrelated version bump hits on a shop that
    already has GL history -- and confirm the count is STILL exactly one.

    THIS TEST WAS RED against this repo's code before the `gl_already_
    existed` guard landed: the second replay posted a SECOND opening-
    balance journal (count == 2), because `_migrate_add_general_ledger`'s
    backfill had no way to tell "first run, real history to seed" apart
    from "N-th replay, already seeded". Quoted verbatim in this wave's
    build report."""
    sch, db_path = _install()
    conn = _open(db_path)
    conn.execute("INSERT INTO customers (id, company_id, name) VALUES (1, 1, 'Cust A')")
    conn.execute("UPDATE customers SET credit_balance=120.500 WHERE id=1")
    conn.execute("INSERT INTO suppliers (id, company_id, name) VALUES (1, 1, 'Supp A')")
    conn.execute("UPDATE suppliers SET credit_balance=45.000 WHERE id=1")
    conn.commit()
    _rewind_to_pre_v38(conn)
    conn.close()

    sch.init_retail()  # v37 -> current: the real, FIRST-ever backfill

    conn = _open(db_path)
    count_after_first = conn.execute(
        "SELECT COUNT(*) FROM gl_journals WHERE source_type='opening_balance'"
    ).fetchone()[0]
    conn.close()
    assert count_after_first == 1, (
        f"the first-ever upgrade itself posted {count_after_first} opening "
        f"balance journals, expected exactly 1 -- broken before this test "
        f"even reaches what it exists to prove"
    )

    _force_second_chain_replay(sch, db_path)  # the LATER, unrelated upgrade

    conn = _open(db_path)
    try:
        count_after_second = conn.execute(
            "SELECT COUNT(*) FROM gl_journals WHERE source_type='opening_balance'"
        ).fetchone()[0]
        assert count_after_second == 1, (
            f"W1-0: a later chain replay re-posted the opening balance -- "
            f"{count_after_second} 'opening_balance' journals exist, "
            f"expected exactly 1. credit_balance never changed between the "
            f"two replays; _migrate_add_general_ledger's own "
            f"gl_already_existed guard should have refused to run the "
            f"backfill again."
        )
    finally:
        conn.close()


def test_an_interrupted_first_upgrade_still_posts_the_opening_balance_once():
    """The ALLOW half of the W1-0 guard (ENGINEERING.md: prove both
    directions of anything that both denies and allows).

    `gl_already_existed` answers "did v38's DDL already run", and that DDL
    is COMMITTED the moment its `executescript` runs. The opening-balance
    INSERT after it is not: v39 and v40 only use `conn.execute`, so nothing
    commits it until v41's own `executescript` (or `ensure_schema_version`'s
    final commit). A v37 install whose upgrade fails anywhere in that window
    is rolled back by `init_retail`'s own `finally` -- tables kept, journal
    discarded -- and `ensure_schema_version` retries next launch. Before the
    guard, the retry re-posted the opening balance (once, correctly). With
    the guard and nothing else, the retry sees `gl_journals` already there
    and posts NOTHING, ever: the shop's pre-v38 AR/AP never reaches the
    ledger and its trial balance never ties to the subledger again.

    Simulated by making the very next chain step (`_migrate_add_cost_basis`,
    v39) raise once, which is exactly where a real v39 data failure would
    surface. RED on the guard-only code: 0 opening journals after the retry.
    """
    sch, db_path = _install()
    conn = _open(db_path)
    conn.execute("INSERT INTO customers (id, company_id, name) VALUES (1, 1, 'Cust A')")
    conn.execute("UPDATE customers SET credit_balance=88.125 WHERE id=1")
    conn.commit()
    _rewind_to_pre_v38(conn)
    conn.close()

    real_step = sch._migrate_add_cost_basis

    def _fail_once(c):
        raise RuntimeError('simulated v39 failure after the v38 step already ran')

    sch._migrate_add_cost_basis = _fail_once
    try:
        with pytest.raises(RuntimeError):
            sch.init_retail()  # first launch: the upgrade fails mid-chain
    finally:
        sch._migrate_add_cost_basis = real_step

    assert _user_version(db_path) == 37, 'fixture bug: the failed upgrade advanced user_version'

    sch.init_retail()  # next launch: the retry ensure_schema_version promises

    conn = _open(db_path)
    try:
        rows = conn.execute(
            "SELECT id FROM gl_journals WHERE company_id=1 AND source_type='opening_balance'"
        ).fetchall()
        assert len(rows) == 1, (
            f"an interrupted first upgrade left {len(rows)} opening-balance "
            f"journal(s) after the retry, expected exactly 1 -- the pre-v38 "
            f"AR balance (88.125) never reached the ledger"
        )
        ar_debit = conn.execute(
            "SELECT SUM(l.debit) FROM gl_journal_lines l "
            "JOIN gl_accounts a ON a.id = l.account_id "
            "WHERE l.journal_id=? AND a.code='1100'", (rows[0]['id'],)
        ).fetchone()[0]
        assert ar_debit == pytest.approx(88.125)
    finally:
        conn.close()


# ── 6, 8. Full v37->v41 upgrade proof ─────────────────────────────────────
#
# These two were written while Builder A's `backfill_legacy_journals` had not
# landed, and carried `xfail(strict=False)`. Both XPASSed once it landed, and a
# non-strict xfail can never go red again -- a later break would have been
# reported as an expected failure. The markers are removed so both enforce.


def test_v37_to_v41_upgrade_posts_one_opening_balance_with_uid_and_one_outbox_event():
    """The full, real upgrade proof this wave's plan asks for
    (BOOKS-WAVE1-PLAN.md, section 6): a v37 database with real AR/AP
    history, upgraded to v41 in one boot, ends up with exactly one
    `opening_balance` journal; that journal carries a real `uid` (not
    NULL); and exactly one `sync_outbox` row was queued for it. The
    boot-time `general_ledger.backfill_legacy_journals` call this file's
    own `_init_retail` wires in (hasattr-guarded -- see that call site's
    own comment) must have both assigned the uid and queued the event, in
    the SAME boot the migration itself ran in.

    Mutation-proved (wave verifier): replacing
    `_v38_insert_opening_journal(...)` in `_migrate_add_general_ledger` with
    the live `_gl.post_journal(...)` (M-B4) makes the v37 -> v41 upgrade fail
    with "no such column: uid" -- `uid` does not exist yet at the v38 step.
    """
    sch, db_path = _install()
    conn = _open(db_path)
    conn.execute("INSERT INTO customers (id, company_id, name) VALUES (1, 1, 'Cust A')")
    conn.execute("UPDATE customers SET credit_balance=100.000 WHERE id=1")
    conn.commit()
    _rewind_to_pre_v38(conn)
    conn.close()

    from core.retail import general_ledger as gl
    assert hasattr(gl, 'backfill_legacy_journals'), (
        "general_ledger.backfill_legacy_journals is missing -- the boot "
        "step in _init_retail is hasattr-guarded, so without it every "
        "legacy journal would silently never get a uid"
    )

    sch.init_retail()  # v37 -> v41 in one boot, boot backfill included

    conn = _open(db_path)
    try:
        journals = conn.execute(
            "SELECT id, uid FROM gl_journals WHERE company_id=1 AND source_type='opening_balance'"
        ).fetchall()
        assert len(journals) == 1
        assert journals[0]['uid'] is not None

        outbox_count = conn.execute(
            "SELECT COUNT(*) FROM sync_outbox WHERE entity_type='gl_journal' AND entity_id=?",
            (journals[0]['uid'],)
        ).fetchone()[0]
        assert outbox_count == 1
    finally:
        conn.close()


def test_boot_backfill_is_idempotent_across_two_boots():
    """`general_ledger.backfill_legacy_journals` must emit each legacy
    journal AT MOST ONCE across repeated boots -- the second boot, after
    the first has already assigned every uid, finds nothing left to do
    and queues nothing new."""
    sch, db_path = _install()
    conn = _open(db_path)
    conn.execute("INSERT INTO customers (id, company_id, name) VALUES (1, 1, 'Cust A')")
    conn.execute("UPDATE customers SET credit_balance=75.000 WHERE id=1")
    conn.commit()
    _rewind_to_pre_v38(conn)
    conn.close()

    sch.init_retail()  # boot 1: v37 -> v41, boot backfill assigns the uid

    conn = _open(db_path)
    uid_after_boot_1 = conn.execute(
        "SELECT uid FROM gl_journals WHERE source_type='opening_balance'"
    ).fetchone()['uid']
    outbox_after_boot_1 = conn.execute("SELECT COUNT(*) FROM sync_outbox").fetchone()[0]
    conn.close()
    assert uid_after_boot_1 is not None

    sch.init_retail()  # boot 2: nothing left to backfill

    conn = _open(db_path)
    try:
        uid_after_boot_2 = conn.execute(
            "SELECT uid FROM gl_journals WHERE source_type='opening_balance'"
        ).fetchone()['uid']
        outbox_after_boot_2 = conn.execute("SELECT COUNT(*) FROM sync_outbox").fetchone()[0]
        assert uid_after_boot_2 == uid_after_boot_1, 'the uid must not change on a second boot'
        assert outbox_after_boot_2 == outbox_after_boot_1, (
            'a second boot queued another outbox event for a journal that '
            'already has its uid -- backfill_legacy_journals is not idempotent'
        )
    finally:
        conn.close()


# ── 7. v41 relies on the pre-existing cursor reset, adds none of its own ─

def test_upgrade_leaves_cursor_relay_url_null():
    """v41 deliberately adds NO reset of its own (this wave's plan,
    section 5, "Cursor reset: v41 does not add its own"):
    `_migrate_add_cheques`'s pre-existing, unconditional `UPDATE sync_
    cursor SET relay_url = NULL WHERE id = 1` (schema.py) already forces a
    one-time re-pull on every chain replay, and that is sufficient -- this
    test only PINS that the existing mechanism still fires on a real
    v37->v41 upgrade, so a future change cannot silently drop it while
    believing v41 carries its own. Mutation-proved: removing that UPDATE
    statement (schema.py's own `_migrate_add_cheques`) turns this RED --
    quoted in this wave's own build report."""
    sch, db_path = _install()
    conn = _open(db_path)
    conn.execute("UPDATE sync_cursor SET relay_url='https://old-relay.example' WHERE id=1")
    conn.commit()
    _rewind_to_pre_v38(conn)
    conn.close()

    sch.init_retail()

    conn = _open(db_path)
    try:
        row = conn.execute("SELECT relay_url FROM sync_cursor WHERE id=1").fetchone()
        assert row['relay_url'] is None, (
            f"sync_cursor.relay_url is {row['relay_url']!r} after a v37->v41 "
            f"upgrade -- expected NULL so the next pull re-fetches everything "
            f"this shop's cursor skipped past while gl_journal events did not "
            f"exist yet (R2a in the plan)"
        )
    finally:
        conn.close()


# ── 9. F5: the replaced guard closes the "nothing committed yet" window ──

def test_a_crash_before_any_company_commits_still_posts_once_on_retry():
    """F5 (BOOKS-WAVE1-PLAN.md decision F5; v2 finding 6) -- closes a
    RESIDUAL window verifier 1's own gap #2 and verifier 2's own finding 6
    both named as still open after the plain `conn.commit()` fix alone: a
    failure INSIDE `_migrate_add_general_ledger` itself, AFTER its own
    `CREATE TABLE IF NOT EXISTS` DDL has already committed (via
    `executescript`, which commits first) but BEFORE this function's own
    end-of-loop `conn.commit()` ever runs. `gl_journals` exists on the
    very next retry, but NOTHING about the opening balance is durably
    committed anywhere.

    With the ORIGINAL guard (`gl_already_existed`, mere table existence,
    captured before the DDL), the retry would read `gl_already_existed ==
    True` (the table exists) and post NOTHING -- the pre-v38 AR/AP balance
    lost FOREVER. F5's replacement (`entry_user_version >= 38` OR a
    PER-COMPANY `opening_balance` existence check) correctly finds no
    `opening_balance` journal for this company yet (nothing was ever
    committed) and posts it fresh on retry.

    Simulated by making `_v38_insert_opening_journal` (the only INSERT
    site this backfill calls) raise on its very FIRST invocation -- "the
    first company's own insert fails, before this function's own
    end-of-loop commit is ever reached", the exact window this fix closes.
    """
    sch, db_path = _install()
    conn = _open(db_path)
    conn.execute("INSERT INTO customers (id, company_id, name) VALUES (1, 1, 'Cust A')")
    conn.execute("UPDATE customers SET credit_balance=64.250 WHERE id=1")
    conn.commit()
    _rewind_to_pre_v38(conn)
    conn.close()

    real_insert = sch._v38_insert_opening_journal
    call_count = {'n': 0}

    def _fail_first_call(*args, **kwargs):
        call_count['n'] += 1
        if call_count['n'] == 1:
            raise RuntimeError(
                'simulated crash mid-backfill, before _migrate_add_general_'
                'ledger\'s own end-of-loop commit is ever reached'
            )
        return real_insert(*args, **kwargs)

    sch._v38_insert_opening_journal = _fail_first_call
    try:
        with pytest.raises(RuntimeError):
            sch.init_retail()  # first launch: DDL commits, the backfill insert raises
    finally:
        sch._v38_insert_opening_journal = real_insert

    assert _user_version(db_path) == 37, 'fixture bug: the failed upgrade advanced user_version'

    conn = _open(db_path)
    try:
        # Sanity: the DDL's own executescript already committed the table,
        # but genuinely NOTHING was posted -- this is the exact state the
        # OLD `gl_already_existed` guard could not tell apart from "already
        # fully handled".
        gl_journals_exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='gl_journals'"
        ).fetchone()
        assert gl_journals_exists is not None, 'fixture bug: the DDL never committed'
        count_before_retry = conn.execute(
            "SELECT COUNT(*) FROM gl_journals WHERE source_type='opening_balance'"
        ).fetchone()[0]
        assert count_before_retry == 0, 'fixture bug: something committed before the retry'
    finally:
        conn.close()

    sch.init_retail()  # the retry ensure_schema_version promises

    conn = _open(db_path)
    try:
        rows = conn.execute(
            "SELECT id FROM gl_journals WHERE company_id=1 AND source_type='opening_balance'"
        ).fetchall()
        assert len(rows) == 1, (
            f"F5: a crash before ANY company's opening balance ever "
            f"committed left {len(rows)} opening-balance journal(s) after "
            f"the retry, expected exactly 1 -- the pre-v38 AR balance "
            f"(64.250) never reached the ledger"
        )
    finally:
        conn.close()


# ── 10. F9: schema.py's inline validation agrees with general_ledger's ──

def test_v38_insert_opening_journal_validation_agrees_with_validated_lines():
    """F9 (BOOKS-WAVE1-PLAN.md decision F9; v1 gap 7): `_v38_insert_
    opening_journal`'s own INLINED line validation (its own docstring:
    "LINE VALIDATION IS INLINED HERE, NOT IMPORTED", a deliberate,
    permanent duplicate of `post_journal`'s invariant) must never quietly
    disagree with `general_ledger.validated_lines` -- the pure function
    the LIVE `post_journal` itself uses -- about which inputs are
    acceptable, INCLUDING F4's round-then-drop rule: a line whose
    debit/credit rounds to zero must be dropped AFTER quantizing on BOTH
    sides, never before.

    Modelled directly on retail_gl_journal_apply_test.py's own
    `test_balance_check_agrees_with_post_journal_on_the_same_lines` (the
    SAME kind of cross-copy parity proof, for the OTHER inlined
    duplicate)."""
    sch, db_path = _install()
    from core.retail import general_ledger as gl
    conn = _open(db_path)
    try:
        cases = [
            ('balanced_3dp', [
                {'account_code': gl.ACCOUNT_AR, 'debit': '4.007'},
                {'account_code': gl.ACCOUNT_EQUITY, 'credit': '4.007'},
            ]),
            ('sub_quantum_only', [  # F4's own round-then-drop case
                {'account_code': gl.ACCOUNT_AR, 'debit': '0.0004'},
                {'account_code': gl.ACCOUNT_EQUITY, 'credit': '0.0004'},
            ]),
            ('negative', [
                {'account_code': gl.ACCOUNT_AR, 'debit': '-1'},
                {'account_code': gl.ACCOUNT_EQUITY, 'credit': '1'},
            ]),
            ('both_sides_nonzero', [
                {'account_code': gl.ACCOUNT_AR, 'debit': '1', 'credit': '1'},
            ]),
            ('off_by_one_fil', [
                {'account_code': gl.ACCOUNT_AR, 'debit': '4.008'},
                {'account_code': gl.ACCOUNT_EQUITY, 'credit': '4.007'},
            ]),
            # Added by the third wave verifier: accepted inputs whose OUTPUT
            # depends on the round-then-drop rule, so the comparison below
            # (what each side would actually book) has something to bite on.
            ('real_plus_sub_quantum_line', [
                {'account_code': gl.ACCOUNT_AR, 'debit': '4.007'},
                {'account_code': gl.ACCOUNT_AP, 'debit': '0.0004'},
                {'account_code': gl.ACCOUNT_EQUITY, 'credit': '4.007'},
            ]),
            ('half_fil_rounds_up', [
                {'account_code': gl.ACCOUNT_AR, 'debit': '4.0065'},
                {'account_code': gl.ACCOUNT_EQUITY, 'credit': '4.007'},
            ]),
        ]
        for label, lines in cases:
            their_verdict = None  # None == accepted
            their_lines = None
            try:
                clean, _, _ = gl.validated_lines([dict(l) for l in lines], 'JOD')
                their_lines = sorted((code, d, c) for code, d, c, _memo in clean)
            except ValueError:
                their_verdict = 'ValueError'
            except gl.GeneralLedgerImbalanceError:
                their_verdict = 'GeneralLedgerImbalanceError'

            our_verdict = None
            our_lines = None
            try:
                jid = sch._v38_insert_opening_journal(
                    conn, 1, None, '2026-09-01', 'opening_balance', None, 'JOD',
                    [dict(l) for l in lines], reference='OPENING-BALANCE',
                )
                our_lines = sorted(
                    (r['code'], Decimal(str(r['debit'])).quantize(Decimal('0.001')),
                     Decimal(str(r['credit'])).quantize(Decimal('0.001')))
                    for r in conn.execute(
                        "SELECT a.code, l.debit, l.credit FROM gl_journal_lines l "
                        "JOIN gl_accounts a ON a.id = l.account_id WHERE l.journal_id=?",
                        (jid,)
                    ).fetchall()
                )
            except ValueError:
                our_verdict = 'ValueError'
            except gl.GeneralLedgerImbalanceError:
                our_verdict = 'GeneralLedgerImbalanceError'

            assert our_verdict == their_verdict, (
                f"{label}: general_ledger.validated_lines said "
                f"{their_verdict!r}, _v38_insert_opening_journal said "
                f"{our_verdict!r} -- the two inline copies disagree"
            )
            assert our_lines == their_lines, (
                f"{label}: both accepted, but would book different lines -- "
                f"validated_lines {their_lines!r}, _v38_insert_opening_journal "
                f"{our_lines!r}"
            )
        conn.rollback()
    finally:
        conn.close()


# ── 11. Third wave verifier (2026-09-26): proofs F5 and F2 were missing ──

def test_a_later_upgrade_posts_no_opening_balance_for_a_shop_that_had_none_at_v38():
    """F5's GLOBAL half had no mutation proof: removing `if entry_user_version
    >= 38: return` left all 12 tests in this file green, because every one of
    them has an opening balance already on the books, so the per-company
    half alone refused the repost.

    The case the global half exists for is a shop that carried NO credit
    when it first reached v38 (so v38 posted no opening balance), then made
    credit sales -- each already posting its AR to the ledger through
    create_sale -- and then crossed a later version. Without the global
    half, that replay finds no opening balance for the company, reads the
    CURRENT credit_balance, and books the already-posted AR a second time as
    an "opening" balance: W1-0's second clause, the one the duplicate
    detector's "posted after a real transaction" rule exists to flag."""
    sch, db_path = _install()  # the chain ran with no credit anywhere
    conn = _open(db_path)
    assert conn.execute(
        "SELECT COUNT(*) FROM gl_journals WHERE source_type='opening_balance'"
    ).fetchone()[0] == 0, 'fixture: a fresh install posted an opening balance'
    conn.execute("INSERT INTO customers (id, company_id, name) VALUES (1, 1, 'Cust A')")
    conn.execute("UPDATE customers SET credit_balance=33.333 WHERE id=1")
    conn.commit()
    conn.close()

    _force_second_chain_replay(sch, db_path)  # a later, unrelated upgrade

    conn = _open(db_path)
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM gl_journals WHERE source_type='opening_balance'"
        ).fetchone()[0]
        assert n == 0, (
            f"a later chain replay posted {n} opening balance(s) for a shop "
            f"that had none at v38 -- AR that create_sale already booked is "
            f"now in the ledger twice"
        )
    finally:
        conn.close()


def _gl_journal_events(db_path):
    conn = _open(db_path)
    try:
        return [
            {'entity_type': r['entity_type'], 'event_type': r['event_type'],
             'entity_id': r['entity_id'], 'payload': json.loads(r['payload'])}
            for r in conn.execute(
                "SELECT * FROM sync_outbox WHERE entity_type='gl_journal' ORDER BY rowid"
            ).fetchall()
        ]
    finally:
        conn.close()


def test_a_pre_v38_backup_restored_on_a_replacement_device_books_the_opening_balance_once_on_a_third_device():
    """F2 as the plan ordered it tested: verifier 2's repro4 end to end, on
    real migrations, not only on `legacy_journal_uid` itself.

    A shop at v37 whose customers owe it 50.005. The ORIGINAL device
    upgrades and sends its opening balance. A REPLACEMENT device restores
    the same pre-v38 backup later: the v38 step regenerates the opening
    balance with a new `posted_at`. A third device receives both outboxes
    and must hold the debt once. RED under the F2 mutation (opening balances
    hashed like any other journal): the third device books AR 100.010."""
    sch, original_db = _install()
    conn = _open(original_db)
    conn.execute("INSERT INTO customers (id, company_id, name) VALUES (1, 1, 'Cust A')")
    conn.execute("UPDATE customers SET credit_balance=50.005 WHERE id=1")
    conn.commit()
    _rewind_to_pre_v38(conn)
    backup_path = original_db + '.pre-v38-backup'
    backup = sqlite3.connect(backup_path)
    conn.backup(backup)
    backup.close()
    conn.close()

    sch.init_retail()  # the ORIGINAL device: v37 -> current, backfill, one event
    original_events = _gl_journal_events(original_db)

    time.sleep(1.1)  # the restore happens later, so posted_at differs
    replacement_root = _fresh_app_data('aura-retail-v41-replacement-')
    sch.BASE_DIR = os.path.join(replacement_root, 'database')
    sch.SUBSYS_DIR = os.path.join(sch.BASE_DIR, 'subsystems')
    os.makedirs(sch.SUBSYS_DIR, exist_ok=True)
    replacement_db = os.path.join(sch.SUBSYS_DIR, 'retail.db')
    shutil.copyfile(backup_path, replacement_db)
    sch.init_retail()  # the REPLACEMENT device: same backup, upgraded later
    replacement_events = _gl_journal_events(replacement_db)

    posted = [e['payload']['posted_at'] for e in original_events + replacement_events]
    assert len(posted) == 2 and posted[0] != posted[1], (
        f"fixture: expected one opening balance per device with different "
        f"posted_at values, got {posted}"
    )

    _, third_db = _install()  # the THIRD device
    from commercial_runtime.sync.sync_service import SyncService
    svc = SyncService(client_factory=lambda: None, get_conn=lambda: _open(third_db),
                      local_company_id_provider=lambda: 1)
    conn = _open(third_db)
    try:
        events = original_events + replacement_events
        svc.apply_pull_result(conn, {'events': events, 'cursor': len(events)})
        conn.commit()
        n = conn.execute(
            "SELECT COUNT(*) FROM gl_journals WHERE source_type='opening_balance'"
        ).fetchone()[0]
        ar = conn.execute(
            "SELECT COALESCE(SUM(l.debit - l.credit), 0) FROM gl_journal_lines l "
            "JOIN gl_accounts a ON a.id = l.account_id WHERE a.company_id=1 AND a.code='1100'"
        ).fetchone()[0]
        assert (n, Decimal(str(ar)).quantize(Decimal('0.001'))) == (1, Decimal('50.005')), (
            f"the third device holds {n} opening balance(s) and AR {ar} -- the "
            f"same imported debt restored on a replacement device was booked "
            f"again (true AR 50.005)"
        )
    finally:
        conn.close()
