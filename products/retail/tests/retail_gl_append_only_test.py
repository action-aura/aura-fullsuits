"""Aura Retail -- ratchet for `trg_gl_journals_no_update`'s value-conditional
append-only guard (database/schema.py::_migrate_add_general_ledger).

WHY THIS FILE EXISTS, SEPARATELY FROM retail_gl_schema_test.py.

`gl_journals` and `gl_journal_lines` are meant to be append-only: correcting
a posted journal means posting a REVERSING journal
(core/retail/general_ledger.py::reverse_journal), never rewriting or
removing one. That is enforced at the database level by four triggers --
see the migration's own "APPEND-ONLY, ENFORCED BY TRIGGER" docstring
section.

Three of those four triggers are an unconditional `RAISE(ABORT, ...)`.
`trg_gl_journals_no_update` is NOT -- it cannot be, because `gl_journals`
carries `company_id` and is therefore discovered by
`company_scoped_tables(conn)`, which makes it a table
`rebind_company_id()` legitimately rewrites: an install can accumulate real
GL history (a return, a customer payment -- both post journals and are on
the pre-activation restricted allowlist) before a licence is ever
activated, and `rebind_company_id_after_activation()` then issues
`UPDATE gl_journals SET company_id=?` over every such row. An unconditional
trigger refused that real path outright --
retail_v13_additive_only_behavioural_test.py's
`test_the_v14_rebind_touches_company_id_and_absolutely_nothing_else` and
`test_a_refused_rebind_leaves_every_row_of_every_table_byte_identical` both
failed with `sqlite3.IntegrityError: gl_journals is append-only` before this
trigger was made value-conditional.

So the trigger's `WHEN` clause names every content column (every column of
`gl_journals` except `company_id`) and fires only when one of THOSE
changed. This file proves two things CLAUDE.md's engineering standard both
name explicitly:

  (a) THE RATCHET. The WHEN clause is compared against the LIVE table shape
      (`PRAGMA table_info`), never against a second hand-typed column list
      -- two copies of the same typed list compare equal while both drift
      from the product (this repo's own MarkGeometryParityContractTest
      lesson). A later migration that adds a column to `gl_journals` without
      also widening this trigger's WHEN clause must fail test (a).

  (b) THE ALLOW-HALF IS THE POINT. A guard that must deny X (tampering with
      posted content) while still allowing Y (the company_id rebind) is
      half-tested if only the deny-half is checked -- the deny-half is the
      obvious one to write. So the company_id-allowed cases here are not
      optional extras; they are the regression this whole change exists to
      prevent. Proven both as a value CHANGE and as a NO-OP rewrite (the
      rebind's own idempotent-replay shape: `UPDATE ... SET company_id=?
      WHERE company_id=?` can re-select a row already on the new id).

  (c) THE REAL PATH, end to end -- not just raw SQL against the trigger:
      post a journal through `core.retail.general_ledger.post_journal`,
      then call the real `rebind_company_id`, and confirm the journal's
      `company_id` moved while every other column is byte-identical.

Follows the SAME bootstrap convention as retail_gl_schema_test.py (no
shared conftest.py exists for products/retail/tests/): its own temp
app-data dir per test via `_install()`, real `init_retail()`, no Flask app
anywhere in this file.

Run:
    pytest products/retail/tests/retail_gl_append_only_test.py -q
"""
import os
import re
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_TMP_DIRS = []

# Deliberately not '1' or any other default-looking id -- must not collide
# with whatever a fresh init_retail() install happens to seed rows under.
OLD_COMPANY_ID = 'gl-ao-test-old-000'
NEW_COMPANY_ID = 'gl-ao-test-new-111'


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
    retail_gl_schema_test.py's own helper."""
    tmp = _fresh_app_data('aura-retail-gl-appendonly-')
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


def _table_columns(conn, table):
    """Column names IN LIVE-SCHEMA ORDER, from PRAGMA table_info -- never a
    hand-typed list. This is the whole point of the ratchet: the expected
    set is derived from the database that was actually built, not from a
    second copy of the same list a future column addition would have to
    remember to update in two places."""
    return [row[1] for row in conn.execute(f'PRAGMA table_info({table})').fetchall()]


def _trigger_sql(conn, name):
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='trigger' AND name=?", (name,)
    ).fetchone()
    return row[0] if row else None


def _seed_journal(conn, gl, cid, memo='seed'):
    """Post one real, balanced journal via the actual posting module --
    never a hand-built INSERT -- so every test here exercises the same
    shape a real route produces. `memo` is passed to BOTH the header
    (gl_journals.memo -- what the tamper/allow-half tests below inspect)
    and the two lines, since post_journal treats them as independent
    columns (gl_journals.memo defaults to None if the header kwarg is
    never supplied, even when every line carries its own memo)."""
    jid = gl.post_journal(
        conn, cid, None, '2026-09-21', 'sale', 1, 'JOD',
        [
            {'account_code': gl.ACCOUNT_CASH, 'debit': 5, 'memo': memo},
            {'account_code': gl.ACCOUNT_SALES_REVENUE, 'credit': 5, 'memo': memo},
        ],
        memo=memo,
    )
    conn.commit()
    return jid


# ── (a) The ratchet: WHEN clause derived-vs-live comparison ────────────────

def test_trigger_when_clause_covers_every_content_column_except_company_id():
    """Read BOTH sides from the live database, never from a hand-typed
    list: PRAGMA table_info for the real columns, sqlite_master for the
    real trigger text. A later migration that adds a column to
    `gl_journals` without widening this WHEN clause must fail here --
    that is the entire reason this test exists rather than a comment."""
    sch, db_path = _install()
    conn = _open(db_path)
    try:
        columns = _table_columns(conn, 'gl_journals')
        assert columns, 'gl_journals has no columns -- PRAGMA table_info returned nothing'
        assert 'company_id' in columns, (
            'gl_journals no longer carries company_id -- the whole premise of '
            'this trigger (it must stay reachable by rebind_company_id) has '
            'changed; this test needs re-reading, not blindly updating'
        )

        sql = _trigger_sql(conn, 'trg_gl_journals_no_update')
        assert sql is not None, 'trg_gl_journals_no_update does not exist'

        content_columns = [c for c in columns if c != 'company_id']
        assert content_columns, 'gl_journals has no content columns besides company_id'
        missing = [
            col for col in content_columns
            if not re.search(r'\bOLD\.' + re.escape(col) + r'\b', sql, re.IGNORECASE)
        ]
        assert not missing, (
            f'trg_gl_journals_no_update\'s WHEN clause does not reference '
            f'OLD.{missing} -- a column was added to gl_journals without '
            f'widening this trigger, so an UPDATE that tampers with '
            f'{missing} alongside company_id would slip through unguarded. '
            f'Trigger SQL:\n{sql}'
        )

        # The other direction of the same ratchet: company_id must NOT be
        # named in the WHEN clause -- if it were, a company_id-only UPDATE
        # (the rebind, including its no-op replay) would be refused again,
        # which is the exact regression this whole trigger change exists to
        # fix.
        assert not re.search(r'\bOLD\.company_id\b', sql, re.IGNORECASE), (
            f'trg_gl_journals_no_update\'s WHEN clause references company_id '
            f'-- that would refuse the legitimate rebind_company_id() UPDATE '
            f'this value-conditional trigger exists to allow. Trigger SQL:\n{sql}'
        )
    finally:
        conn.close()


# ── (b) Behavioural proof, both directions -- deny-half AND allow-half ─────

def test_content_update_on_gl_journals_is_refused():
    sch, db_path = _install()
    conn = _open(db_path)
    try:
        from core.retail import general_ledger as gl
        jid = _seed_journal(conn, gl, 1)

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE gl_journals SET memo='tampered' WHERE id=?", (jid,))

        row = conn.execute("SELECT memo FROM gl_journals WHERE id=?", (jid,)).fetchone()
        assert row[0] == 'seed', 'a blocked UPDATE must not have changed the value anyway'
    finally:
        conn.close()


def test_company_id_only_update_on_gl_journals_is_allowed_when_value_changes():
    """THE ALLOW-HALF. This is the exact statement rebind_company_id()
    issues in production: SET company_id=<new> WHERE company_id=<old>,
    changing nothing else. Must NOT raise."""
    sch, db_path = _install()
    conn = _open(db_path)
    try:
        from core.retail import general_ledger as gl
        jid = _seed_journal(conn, gl, OLD_COMPANY_ID)

        conn.execute(
            "UPDATE gl_journals SET company_id=? WHERE id=?", (NEW_COMPANY_ID, jid)
        )
        conn.commit()

        row = conn.execute(
            "SELECT company_id, memo FROM gl_journals WHERE id=?", (jid,)
        ).fetchone()
        assert row['company_id'] == NEW_COMPANY_ID
        assert row['memo'] == 'seed', 'company_id moved but content changed too -- unexpected'
    finally:
        conn.close()


def test_company_id_only_update_on_gl_journals_is_allowed_as_a_noop():
    """THE OTHER ALLOW-HALF, explicitly named in the fix's own rationale: a
    NO-OP rebind that writes back the value a row already has (rebind_
    company_id's own count-verification re-reads and re-writes rows that
    matched the WHERE clause; a row already on the target id never matches
    that WHERE in production, but this proves the trigger itself does not
    depend on the value actually differing -- OLD.company_id IS NOT
    NEW.company_id would be false for a same-value write, so this is really
    proving the WHEN clause's OR-chain doesn't spuriously fire on a
    same-value company_id write bundled with no other change). Must NOT
    raise."""
    sch, db_path = _install()
    conn = _open(db_path)
    try:
        from core.retail import general_ledger as gl
        jid = _seed_journal(conn, gl, OLD_COMPANY_ID)

        conn.execute(
            "UPDATE gl_journals SET company_id=? WHERE id=?", (OLD_COMPANY_ID, jid)
        )
        conn.commit()

        row = conn.execute(
            "SELECT company_id, memo FROM gl_journals WHERE id=?", (jid,)
        ).fetchone()
        assert row['company_id'] == OLD_COMPANY_ID
        assert row['memo'] == 'seed'
    finally:
        conn.close()


def test_delete_on_gl_journals_is_refused():
    sch, db_path = _install()
    conn = _open(db_path)
    try:
        from core.retail import general_ledger as gl
        jid = _seed_journal(conn, gl, 1)

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM gl_journals WHERE id=?", (jid,))

        still_there = conn.execute(
            "SELECT COUNT(*) FROM gl_journals WHERE id=?", (jid,)).fetchone()[0]
        assert still_there == 1
    finally:
        conn.close()


def test_gl_journal_lines_update_is_still_unconditionally_refused():
    """gl_journal_lines carries NO company_id (see the migration's own
    docstring -- it scopes entirely through journal_id), so it is never
    discovered by company_scoped_tables and rebind_company_id never touches
    it. Its two triggers stay exactly as they were: unconditional. This
    test exists so a future accidental "relax gl_journal_lines too" edit is
    caught here, not just left to trust."""
    sch, db_path = _install()
    conn = _open(db_path)
    try:
        from core.retail import general_ledger as gl
        jid = _seed_journal(conn, gl, 1)
        line_id = conn.execute(
            "SELECT id FROM gl_journal_lines WHERE journal_id=? LIMIT 1", (jid,)
        ).fetchone()[0]

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE gl_journal_lines SET debit=999 WHERE id=?", (line_id,))

        row = conn.execute(
            "SELECT debit FROM gl_journal_lines WHERE id=?", (line_id,)).fetchone()
        assert row[0] == 5.0
    finally:
        conn.close()


def test_gl_journal_lines_delete_is_still_unconditionally_refused():
    sch, db_path = _install()
    conn = _open(db_path)
    try:
        from core.retail import general_ledger as gl
        jid = _seed_journal(conn, gl, 1)
        line_id = conn.execute(
            "SELECT id FROM gl_journal_lines WHERE journal_id=? LIMIT 1", (jid,)
        ).fetchone()[0]

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM gl_journal_lines WHERE id=?", (line_id,))
    finally:
        conn.close()


# ── (c) The real path, end to end: post -> rebind -> verify ────────────────

def test_rebind_company_id_moves_gl_journals_company_id_and_nothing_else():
    """Drives the ACTUAL production path this trigger fix exists for: a
    real journal posted through general_ledger.post_journal, then the real
    schema.rebind_company_id -- not raw SQL standing in for it. Confirms
    the rebind succeeds (it used to raise sqlite3.IntegrityError before the
    trigger was made value-conditional) and that ONLY company_id moved on
    the journal row -- every other column, compared by name via
    sqlite3.Row's keys() rather than a hand-typed column list, is byte-
    identical before and after."""
    sch, db_path = _install()
    conn = _open(db_path)
    try:
        from core.retail import general_ledger as gl
        jid = _seed_journal(conn, gl, OLD_COMPANY_ID, memo='real path')

        before = dict(conn.execute(
            "SELECT * FROM gl_journals WHERE id=?", (jid,)
        ).fetchone())
        assert before['company_id'] == OLD_COMPANY_ID

        result = sch.rebind_company_id(conn, NEW_COMPANY_ID, OLD_COMPANY_ID)
        conn.commit()
        assert result['status'] == 'rebound', result

        after = dict(conn.execute(
            "SELECT * FROM gl_journals WHERE id=?", (jid,)
        ).fetchone())

        assert after['company_id'] == NEW_COMPANY_ID
        for col in before:
            if col == 'company_id':
                continue
            assert after[col] == before[col], (
                f'gl_journals.{col} changed during rebind: '
                f'{before[col]!r} -> {after[col]!r} (only company_id should move)'
            )
        assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    finally:
        conn.close()
