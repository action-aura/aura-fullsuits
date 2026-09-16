"""
Aura Retail -- per-document-type numbering series, schema v34 (ROADMAP.md's
2026-09-15 "schema versions v32-v35 RESERVED" entry, v34 -- the Aseel
تعدد الدفاتر / "multi-ledger" equivalent). Migration-layer proof only; see
`retail_doc_series_test.py` for allocation/gaplessness and
`retail_doc_series_einvoice_firewall_test.py` for the e-invoicing firewall.

Shaped on `retail_site_relay_schema_test.py` (v30's own migration test):
a REAL install through `database.schema.init_retail()`, never this
migration's own DDL string executed directly.

WHAT THIS FILE PROVES:

  1. Both tables and both indexes exist after a real migration, and
     `PRAGMA user_version` advanced to at least 34.
  2. Re-running `_migrate_add_doc_series` a second time is a no-op (row
     counts and `sqlite_master` objects unchanged).
  3. `idx_doc_series_code` is genuinely UNIQUE on `(doc_type, code)` -- the
     SAME code is refused for the SAME doc_type but allowed again under a
     DIFFERENT doc_type (the C1 allow-half: a sale book 'A' and a return
     book 'A' must be able to coexist).
  4. A database whose `user_version` was stamped BELOW 34 (simulating an
     install that merged before a later A-PAR wave, or that never saw
     v32/v33 at all) still receives both tables when
     `ensure_schema_version` is invoked again -- `_migrate_retail_schema`
     runs every step unconditionally regardless of the stamped starting
     version (this file's own module docstring), so doc_series's
     self-contained, dependency-free tables land the same way no matter
     which version the database walked in at.
  5. THE MECHANICAL MERGE-ORDER GUARD (residual risk #1, shape (b)):
     `RETAIL_SCHEMA_VERSION >= 34`. If a later A-PAR wave's merge resolves
     this constant back DOWN, this assertion fails CI before a fleet of
     already-upgraded tills hits `MigrationError` at boot
     (`commercial_runtime/security/migration_safety.py`, "no safe
     automatic downgrade").
  6. ONE VOCABULARY, ONE OBJECT, proved rather than asserted in a comment:
     `set(DOC_SERIES_TYPES) <= set(REF_PREFIX)` and
     `doc_series.REF_PREFIX is retail_api._REF_PREFIX` -- the move (not
     copy) `core/retail/doc_series.py` makes of retail_api.py's
     pre-existing `_REF_PREFIX` dict.

MUTATION PROOFS (run by hand while writing this file, both directions
quoted in this session's own report rather than encoded as test code --
the same convention `retail_site_relay_schema_test.py`'s own docstring
already states for its sibling file):
  * dropped `IF NOT EXISTS` from `idx_doc_series_lookup` -> test 2
    (`test_rerunning_migration_is_a_no_op`) raised
    `sqlite3.OperationalError: index idx_doc_series_lookup already exists`
    on the second call -- RED. Restored -> GREEN.
  * changed `idx_doc_series_code` from `UNIQUE(doc_type, code)` to
    `UNIQUE(code)` -> test 3's allow-half
    (`test_same_code_is_allowed_under_a_different_doc_type`) raised
    `sqlite3.IntegrityError` on the second INSERT -- RED. Restored -> GREEN.
  * set `RETAIL_SCHEMA_VERSION = 32` -> test 5
    (`test_schema_version_has_not_regressed_below_v34`) failed its bare
    `>= 34` assertion -- RED. Restored to 34 -> GREEN.

Self-contained bootstrap; no shared conftest.py. CRITICAL: exactly ONE
pytest process per file (AUDIT-010).

Run:
    C:/Users/MSI/Desktop/aura-fullsuits/.venv/Scripts/python.exe -m pytest \
        products/retail/tests/retail_doc_series_migration_test.py -v
"""
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

PRODUCT_DIR = Path(__file__).resolve().parents[1]        # products/retail
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent                    # aura-fullsuits
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_TMP_DIRS = []

DOC_SERIES_TABLES = ['doc_series', 'doc_series_counter']


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
    boot path, the full v0 -> v34 migration chain."""
    tmp = _fresh_app_data('aura-retail-doc-series-')
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


# ── 1. Both tables + both indexes exist, version advanced ──────────────────

def test_both_tables_exist_and_the_version_advanced_past_v34():
    """`>= 34`, not `== 34` -- see retail_site_relay_schema_test.py's
    identical v30 test for why a hard equality breaks on the next bump for
    no gain, and why comparing against the live RETAIL_SCHEMA_VERSION
    constant instead would be the exact "two values that move together"
    weakening ENGINEERING.md names."""
    sch, db_path = _install()
    conn = _open(db_path)
    try:
        assert conn.execute('PRAGMA user_version').fetchone()[0] >= 34

        live_tables = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        for table in DOC_SERIES_TABLES:
            assert table in live_tables, f"{table} must exist after migrating to v34"

        live_indexes = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()
        }
        assert 'idx_doc_series_code' in live_indexes
        assert 'idx_doc_series_lookup' in live_indexes
    finally:
        conn.close()


# ── 2. Re-running the migration is a no-op ─────────────────────────────────

def test_rerunning_migration_is_a_no_op():
    sch, db_path = _install()
    conn = _open(db_path)
    try:
        counts_before = {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in DOC_SERIES_TABLES
        }
        objects_before = [
            dict(r) for r in conn.execute(
                "SELECT type, name FROM sqlite_master WHERE name LIKE 'doc\\_series%' ESCAPE '\\' "
                "ORDER BY type, name"
            )
        ]

        sch._migrate_add_doc_series(conn)  # second, unnecessary pass
        conn.commit()

        counts_after = {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in DOC_SERIES_TABLES
        }
        objects_after = [
            dict(r) for r in conn.execute(
                "SELECT type, name FROM sqlite_master WHERE name LIKE 'doc\\_series%' ESCAPE '\\' "
                "ORDER BY type, name"
            )
        ]

        assert counts_before == counts_after, \
            "a redundant second call must not change any doc_series* table's row count"
        assert objects_before == objects_after, \
            "sqlite_master's own doc_series* object list must be byte-identical after a redundant second call"
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == 'ok'
    finally:
        conn.close()


# ── 3. idx_doc_series_code: (doc_type, code), not code alone (C1) ──────────

def test_duplicate_code_within_one_doc_type_is_refused():
    sch, db_path = _install()
    conn = _open(db_path)
    try:
        conn.execute(
            "INSERT INTO doc_series (id, company_id, doc_type, code, label) "
            "VALUES ('s1', 1, 'sale', 'A', 'Book A')"
        )
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO doc_series (id, company_id, doc_type, code, label) "
                "VALUES ('s2', 1, 'sale', 'A', 'Second Book A')"
            )
    finally:
        conn.close()


def test_same_code_is_allowed_under_a_different_doc_type():
    """THE ALLOW-HALF -- a sale book 'A' and a return book 'A' must be free
    to coexist: they render into two different columns
    (sales.sale_number vs returns.return_number), and forbidding this would
    block the first thing a shop tries. MUT: restore `UNIQUE(code)` (drop
    `doc_type` from the index) -> RED, confirmed while writing this test."""
    sch, db_path = _install()
    conn = _open(db_path)
    try:
        conn.execute(
            "INSERT INTO doc_series (id, company_id, doc_type, code, label) "
            "VALUES ('s1', 1, 'sale', 'A', 'Sale Book A')"
        )
        conn.execute(
            "INSERT INTO doc_series (id, company_id, doc_type, code, label) "
            "VALUES ('s2', 1, 'return', 'A', 'Return Book A')"
        )
        conn.commit()  # must not raise
        rows = conn.execute("SELECT doc_type FROM doc_series ORDER BY doc_type").fetchall()
        assert [r['doc_type'] for r in rows] == ['return', 'sale']
    finally:
        conn.close()


# ── 4. A database stamped below v34 still receives both tables ────────────

def test_a_database_stamped_below_v34_still_receives_both_tables_on_reentry():
    """Simulates the merge-order hazard's benign direction: an install
    whose on-disk `user_version` is below 34 (it merged before a later
    A-PAR wave landed, or it never ran v32/v33 at all) still gets
    doc_series/doc_series_counter the next time `ensure_schema_version`
    runs, because `_migrate_retail_schema` calls every step
    UNCONDITIONALLY regardless of the stamped starting version (this
    function's own module docstring: "a v1 install upgrading straight to
    v7 must run ALL steps in one pass"). doc_series's own CREATE TABLE/
    INDEX statements read, ALTER and depend on nothing else in the chain,
    so this holds for ANY starting version below 34, not just 31 or 33
    specifically."""
    sch, db_path = _install()
    conn = _open(db_path)
    try:
        # Roll the stamped version back WITHOUT touching any table --
        # `PRAGMA user_version` is a bare integer in the sqlite file header,
        # independent of the schema it actually describes. This is the
        # in-file equivalent of "an install that merged before this wave".
        conn.execute('PRAGMA user_version = 31')
        conn.commit()
        assert conn.execute('PRAGMA user_version').fetchone()[0] == 31

        backup_dir = tempfile.mkdtemp(prefix='aura-retail-doc-series-backup-')
        _TMP_DIRS.append(backup_dir)
        from commercial_runtime.security.migration_safety import ensure_schema_version
        ensure_schema_version(conn, db_path, sch.RETAIL_SCHEMA_VERSION,
                               sch._migrate_retail_schema, backup_dir)

        assert conn.execute('PRAGMA user_version').fetchone()[0] == sch.RETAIL_SCHEMA_VERSION
        live_tables = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        for table in DOC_SERIES_TABLES:
            assert table in live_tables
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == 'ok'
    finally:
        conn.close()


# ── 5. The mechanical merge-order guard ────────────────────────────────────

def test_schema_version_has_not_regressed_below_v34():
    """Residual risk #1, shape (b): a later A-PAR wave's merge resolving
    `RETAIL_SCHEMA_VERSION` back down would make every already-upgraded
    till refuse to boot (`MigrationError`, "no safe automatic downgrade").
    This is the CI-side tripwire for that shape -- cheap, and it fails
    loudly before any fleet does."""
    import database.schema as sch
    assert sch.RETAIL_SCHEMA_VERSION >= 34


# ── 6. One vocabulary, one object ──────────────────────────────────────────

def test_doc_series_types_is_a_subset_of_the_moved_ref_prefix_and_is_the_same_object():
    """C6's fix, proved rather than asserted in a comment: `REF_PREFIX` was
    MOVED (not copied) from retail_api.py into core/retail/doc_series.py,
    and retail_api.py aliases it. Two independently-typed copies of the
    same vocabulary is the exact "the two literals must match" shape
    sync_service.py's own module docstring already names as a bug pattern
    this codebase has hit before -- this test is what stops it recurring
    here. MUT: reintroduce a second, separately-typed dict in either module
    -> `is` fails -> RED, confirmed while writing this test."""
    import importlib
    doc_series = importlib.import_module('core.retail.doc_series')
    retail_api = importlib.import_module('api.retail_api')
    assert set(doc_series.DOC_SERIES_TYPES) <= set(doc_series.REF_PREFIX)
    assert doc_series.REF_PREFIX is retail_api._REF_PREFIX
