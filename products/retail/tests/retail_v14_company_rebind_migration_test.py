"""
Aura Retail -- schema v14 regression coverage: rebinding `company_id` from
the locally-derived `md5(admin_email)` to the Owner-issued tenant key
(ROADMAP.md's 2026-08-21 reservation, Phase 2 of
docs/launch-readiness/multi-device-design.md). See
database/schema.py::rebind_company_id and
::_migrate_rebind_company_id_to_owner_issued.

Why this needs its own hard test bed: `company_id` is the tenant key on
EVERY business row in retail.db. `api/retail_api.py::_cid()` filters
essentially every query on it, so a rebind that half-lands does not raise --
it makes a real shop's entire data set silently invisible. The tests below
therefore assert the three things that distinguish a correct rebind from a
plausible-looking one:

  1. Every scoped table moves, in one transaction, with row counts verified
     before and after and NOTHING left behind on the old id.
  2. Re-running is a clean no-op (so an interrupted rebind can be retried on
     the next launch, which is the only recovery this design has).
  3. With no licence present it is a genuine no-op -- not an error, not a
     partial write -- and the schema version still advances, because v14 is
     about being ABLE to rebind, not about having done it.

Run:
    pytest products/retail/tests/retail_v14_company_rebind_migration_test.py -v
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

LEGACY_COMPANY_ID = 'd41d8cd98f00b204e9800998ecf8427e'   # a real md5 shape
OWNER_COMPANY_ID = '7c9e6679-7425-40de-944b-e07fc1f90ae7'  # a real Owner licence uuid

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


def _fresh_install():
    """A real install (full v0 -> current chain, real seed data), then every
    scoped row moved onto a legacy md5-shaped company_id -- i.e. exactly what
    an install that onboarded before Owner ever issued it a tenant key looks
    like."""
    tmp = _fresh_app_data('aura-retail-v14-')

    import database.schema as sch
    sch.BASE_DIR = os.path.join(tmp, 'database')
    sch.SUBSYS_DIR = os.path.join(sch.BASE_DIR, 'subsystems')
    sch.init_retail()

    db_path = os.path.join(sch.SUBSYS_DIR, 'retail.db')
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')

    for table in sch.company_scoped_tables(conn):
        conn.execute(f'UPDATE {table} SET company_id=?', (LEGACY_COMPANY_ID,))
    conn.commit()
    return tmp, conn


def _scoped_counts(conn, tables, company_id):
    return {
        t: conn.execute(f'SELECT COUNT(*) FROM {t} WHERE company_id=?', (company_id,)).fetchone()[0]
        for t in tables
    }


def _write_licence_state(app_data, license_public_id, *, envelope=True):
    """Write the licensing_state row `owner_issued_company_id()` reads.

    Written with plain sqlite3/json rather than by importing
    LicenseStateRepository, mirroring how the reader itself is written: the
    reader must stay importable on Android, where importing
    licensing_contracts pulls in `cryptography` at module scope (see
    device_context.py's module docstring for the crash this avoids). A test
    that reached for the repository would be exercising a different code
    path from the one that actually ships.
    """
    db_dir = os.path.join(app_data, 'database', 'subsystems')
    os.makedirs(db_dir, exist_ok=True)
    conn = sqlite3.connect(os.path.join(db_dir, 'licensing.db'))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS licensing_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            licensing_schema_version INTEGER NOT NULL,
            product_code TEXT NOT NULL,
            platform TEXT NOT NULL,
            current_state TEXT NOT NULL,
            owner_installation_id TEXT,
            assertion_envelope_json TEXT,
            updated_at TEXT NOT NULL
        )
    """)
    payload = {
        'assertion_id': str(uuid.uuid4()),
        'product_code': 'AURA_RETAIL',
        'license_public_id': license_public_id,
        'installation_public_id': str(uuid.uuid4()),
    }
    conn.execute('DELETE FROM licensing_state')
    conn.execute(
        'INSERT INTO licensing_state (id, licensing_schema_version, product_code, platform, '
        'current_state, owner_installation_id, assertion_envelope_json, updated_at) '
        'VALUES (1,1,?,?,?,?,?,?)',
        ('AURA_RETAIL', 'WINDOWS', 'ACTIVE_ONLINE', payload['installation_public_id'],
         json.dumps({'payload': payload}) if envelope else None,
         '2026-08-21T00:00:00'),
    )
    conn.commit()
    conn.close()


def _write_registry_company_id(app_data, company_id):
    """The install's locally-authoritative tenant key, as
    `mt_auth.create_session` reads it into `session['company_id']` and as
    `sync_service.local_company_id_from_registry()` reads it for pulled
    rows."""
    db_dir = os.path.join(app_data, 'database')
    os.makedirs(db_dir, exist_ok=True)
    conn = sqlite3.connect(os.path.join(db_dir, 'registry.db'))
    conn.execute(
        'CREATE TABLE IF NOT EXISTS company_settings ('
        'id TEXT PRIMARY KEY, company_id TEXT NOT NULL UNIQUE, country TEXT)'
    )
    conn.execute('DELETE FROM company_settings')
    conn.execute('INSERT INTO company_settings (id, company_id) VALUES (?,?)',
                 (str(uuid.uuid4()), company_id))
    conn.commit()
    conn.close()


# ── 1. the rebind itself ────────────────────────────────────────────────────

def test_rebind_moves_every_scoped_table_and_leaves_nothing_on_the_old_id():
    from database.schema import company_scoped_tables, rebind_company_id

    _tmp, conn = _fresh_install()
    tables = company_scoped_tables(conn)
    # ~13 in the reservation's own words; assert a floor rather than an exact
    # number so a future additive migration that adds another scoped table
    # does not fail this test for the wrong reason.
    assert len(tables) >= 13, f'only found {len(tables)} scoped tables: {tables}'
    for expected in ('sales', 'returns', 'products', 'customers', 'suppliers',
                     'branches', 'payments', 'inventory_movements',
                     'inventory_balances', 'purchase_orders', 'categories',
                     'tax_rates', 'journal_entries', 'audit_log'):
        assert expected in tables, f'{expected} is tenant-scoped but was not discovered'
    # Line tables deliberately are NOT in the list: sale_items/return_items/
    # purchase_order_items carry no company_id of their own, they scope
    # entirely through their parent (the same shape cash_movements uses via
    # session_id). Discovering them here would mean somebody had added a
    # redundant second copy of the tenant key.
    for absent in ('sale_items', 'return_items', 'purchase_order_items', 'sync_outbox'):
        assert absent not in tables

    before_total = {t: conn.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0] for t in tables}
    before_legacy = _scoped_counts(conn, tables, LEGACY_COMPANY_ID)
    assert sum(before_legacy.values()) > 0, 'fixture has no scoped rows to move'

    result = rebind_company_id(conn, OWNER_COMPANY_ID)
    conn.commit()

    assert result['status'] == 'rebound', result
    assert result['old_company_id'] == LEGACY_COMPANY_ID
    assert result['new_company_id'] == OWNER_COMPANY_ID
    assert result['rows'] == sum(before_legacy.values())

    after_total = {t: conn.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0] for t in tables}
    assert after_total == before_total, 'the rebind changed a row count'

    after_new = _scoped_counts(conn, tables, OWNER_COMPANY_ID)
    assert after_new == before_legacy, 'not every row landed on the new id'

    stranded = _scoped_counts(conn, tables, LEGACY_COMPANY_ID)
    assert sum(stranded.values()) == 0, f'rows left on the old tenant key: {stranded}'
    conn.close()


def test_rebind_is_idempotent():
    from database.schema import company_scoped_tables, rebind_company_id

    _tmp, conn = _fresh_install()
    tables = company_scoped_tables(conn)
    rebind_company_id(conn, OWNER_COMPANY_ID)
    conn.commit()
    snapshot = _scoped_counts(conn, tables, OWNER_COMPANY_ID)

    second = rebind_company_id(conn, OWNER_COMPANY_ID)
    conn.commit()

    assert second['status'] == 'already_bound', second
    assert second['rows'] == 0
    assert _scoped_counts(conn, tables, OWNER_COMPANY_ID) == snapshot
    assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    conn.close()


def test_rebind_finishes_an_install_that_holds_rows_under_both_ids():
    """The interrupted-rebind state. `rebind_company_id` derives the old id
    from retail.db's own rows precisely so this converges instead of
    deadlocking on "which of these two is the real tenant?"."""
    from database.schema import company_scoped_tables, rebind_company_id

    _tmp, conn = _fresh_install()
    tables = company_scoped_tables(conn)
    # Simulate a crash midway: `sales` moved, everything else did not.
    conn.execute('UPDATE sales SET company_id=?', (OWNER_COMPANY_ID,))
    conn.commit()

    result = rebind_company_id(conn, OWNER_COMPANY_ID)
    conn.commit()

    assert result['status'] == 'rebound', result
    assert result['old_company_id'] == LEGACY_COMPANY_ID
    assert sum(_scoped_counts(conn, tables, LEGACY_COMPANY_ID).values()) == 0
    conn.close()


def test_rebind_refuses_rather_than_merging_two_unrelated_tenants():
    """CLAUDE.md: "one install *can* host more than one company". Blanket-
    moving every row onto one licence's id would merge two tenants' books
    into one -- unrecoverable, and it would look like a successful
    migration. When the old id cannot be identified unambiguously this
    refuses and changes nothing."""
    from database.schema import CompanyRebindError, company_scoped_tables, rebind_company_id

    _tmp, conn = _fresh_install()
    tables = company_scoped_tables(conn)
    second_tenant = 'ffffffffffffffffffffffffffffffff'
    conn.execute('UPDATE sales SET company_id=?', (second_tenant,))
    conn.commit()
    before = _scoped_counts(conn, tables, LEGACY_COMPANY_ID)

    with pytest.raises(CompanyRebindError):
        rebind_company_id(conn, OWNER_COMPANY_ID)

    assert _scoped_counts(conn, tables, LEGACY_COMPANY_ID) == before, 'refusal was not clean'
    assert conn.execute('SELECT COUNT(*) FROM sales WHERE company_id=?',
                        (second_tenant,)).fetchone()[0] > 0
    conn.close()


def test_rebind_with_an_explicit_old_id_moves_only_that_tenant():
    """The multi-tenant escape hatch: told exactly which tenant to move, it
    moves that one and leaves the other alone."""
    from database.schema import rebind_company_id

    _tmp, conn = _fresh_install()
    second_tenant = 'ffffffffffffffffffffffffffffffff'
    conn.execute('UPDATE sales SET company_id=?', (second_tenant,))
    conn.commit()
    others = conn.execute('SELECT COUNT(*) FROM sales WHERE company_id=?',
                          (second_tenant,)).fetchone()[0]

    result = rebind_company_id(conn, OWNER_COMPANY_ID, old_company_id=LEGACY_COMPANY_ID)
    conn.commit()

    assert result['status'] == 'rebound'
    assert conn.execute('SELECT COUNT(*) FROM sales WHERE company_id=?',
                        (second_tenant,)).fetchone()[0] == others
    assert conn.execute('SELECT COUNT(*) FROM products WHERE company_id=?',
                        (OWNER_COMPANY_ID,)).fetchone()[0] > 0
    conn.close()


def test_rebind_is_a_no_op_not_an_error_when_no_owner_issued_id_exists():
    """(d) in the design: licensing is OFF by default in this product, so the
    common case is an install that migrates long before it ever activates."""
    from database.schema import company_scoped_tables, rebind_company_id

    _tmp, conn = _fresh_install()
    tables = company_scoped_tables(conn)
    before = _scoped_counts(conn, tables, LEGACY_COMPANY_ID)

    for absent in (None, '', '   '):
        result = rebind_company_id(conn, absent)
        assert result['status'] == 'skipped', result
        assert result['rows'] == 0

    assert _scoped_counts(conn, tables, LEGACY_COMPANY_ID) == before
    conn.close()


def test_rebind_rolls_back_completely_when_one_table_fails_mid_flight():
    """One transaction, not thirteen. If the rewrite cannot complete, the
    tenant key must be left entirely on the old value -- a database with
    six tables moved and seven not is the exact silent-invisibility state
    this whole migration exists to avoid."""
    from database.schema import company_scoped_tables, rebind_company_id

    _tmp, conn = _fresh_install()
    db_path = conn.execute('PRAGMA database_list').fetchone()[2]
    tables = company_scoped_tables(conn)
    before = _scoped_counts(conn, tables, LEGACY_COMPANY_ID)
    conn.close()

    # `sqlite3.Connection.execute` is read-only on the C type, so the failure
    # is injected through a Connection subclass rather than a monkeypatched
    # attribute -- same connection object the production code would get,
    # just one that gives up partway through the rewrite.
    class FlakyConnection(sqlite3.Connection):
        updates = 0

        def execute(self, sql, *args):
            if sql.lstrip().upper().startswith('UPDATE'):
                FlakyConnection.updates += 1
                if FlakyConnection.updates > 3:
                    raise sqlite3.OperationalError('simulated disk failure mid-rebind')
            return sqlite3.Connection.execute(self, sql, *args)

    conn = sqlite3.connect(db_path, factory=FlakyConnection)
    conn.row_factory = sqlite3.Row
    with pytest.raises(sqlite3.OperationalError):
        rebind_company_id(conn, OWNER_COMPANY_ID)
    conn.close()

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    assert _scoped_counts(conn, tables, LEGACY_COMPANY_ID) == before, \
        'a failed rebind left rows split across two tenant keys'
    assert sum(_scoped_counts(conn, tables, OWNER_COMPANY_ID).values()) == 0
    conn.close()


# ── 2. where the Owner-issued id comes from ─────────────────────────────────

def test_owner_issued_company_id_reads_license_public_id_from_the_stored_assertion():
    from database.schema import owner_issued_company_id

    tmp = _fresh_app_data('aura-retail-v14-lic-')
    assert owner_issued_company_id(tmp) is None, 'unlicensed install must report nothing'

    _write_licence_state(tmp, OWNER_COMPANY_ID)
    assert owner_issued_company_id(tmp) == OWNER_COMPANY_ID


def test_owner_issued_company_id_never_raises_on_a_damaged_or_absent_licence_db():
    from database.schema import owner_issued_company_id

    tmp = _fresh_app_data('aura-retail-v14-lic-bad-')
    _write_licence_state(tmp, OWNER_COMPANY_ID, envelope=False)
    assert owner_issued_company_id(tmp) is None

    db_dir = os.path.join(tmp, 'database', 'subsystems')
    with open(os.path.join(db_dir, 'licensing.db'), 'wb') as f:
        f.write(b'this is not a sqlite database')
    assert owner_issued_company_id(tmp) is None


# ── 3. the migration step ───────────────────────────────────────────────────

def test_v14_migration_is_a_no_op_and_the_version_still_advances_without_a_licence():
    """The overwhelmingly common install: OWNER_LICENSING_BASE_URL unset, so
    there is no Owner-issued id and never was one. v14 must still complete,
    or every later migration is blocked behind a version marker that never
    advances."""
    tmp = _fresh_app_data('aura-retail-v14-unlicensed-')

    import database.schema as sch
    sch.BASE_DIR = os.path.join(tmp, 'database')
    sch.SUBSYS_DIR = os.path.join(sch.BASE_DIR, 'subsystems')
    sch.init_retail()

    conn = sqlite3.connect(os.path.join(sch.SUBSYS_DIR, 'retail.db'))
    conn.row_factory = sqlite3.Row
    assert conn.execute('PRAGMA user_version').fetchone()[0] == sch.RETAIL_SCHEMA_VERSION
    assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    # Seed data keeps the company_id it was written with -- nothing guessed.
    assert conn.execute('SELECT COUNT(*) FROM products WHERE company_id=1').fetchone()[0] > 0
    conn.close()


def test_v14_migration_refuses_to_move_retail_ahead_of_the_identity_layer():
    """THE outage guard, and the reason this function is not a bare
    `rebind_company_id(conn, owner_issued_company_id())`.

    `session['company_id']` is populated from registry.db's `users` row
    (`mt_auth.create_session`), and `retail_api._cid()` filters every query
    on it. Moving retail.db's rows to the Owner-issued key while the
    identity layer still says `md5(admin_email)` means every
    `WHERE company_id=?` matches zero rows: the shop's entire history
    disappears from the UI with no error anywhere. So the retail side only
    ever CONVERGES onto a tenant key the identity layer has already adopted
    -- the same direction `sync_service._apply_event` already takes when it
    stamps the RECEIVING device's own company_id onto a pulled row instead
    of trusting the payload's.
    """
    tmp, conn = _fresh_install()
    from database.schema import _migrate_rebind_company_id_to_owner_issued, company_scoped_tables

    tables = company_scoped_tables(conn)
    _write_licence_state(tmp, OWNER_COMPANY_ID)
    _write_registry_company_id(tmp, LEGACY_COMPANY_ID)   # identity has NOT moved
    before = _scoped_counts(conn, tables, LEGACY_COMPANY_ID)

    result = _migrate_rebind_company_id_to_owner_issued(conn)
    conn.commit()

    assert result['status'] == 'deferred', result
    assert _scoped_counts(conn, tables, LEGACY_COMPANY_ID) == before
    conn.close()


def test_v14_migration_rebinds_once_the_identity_layer_holds_the_owner_issued_id():
    tmp, conn = _fresh_install()
    from database.schema import _migrate_rebind_company_id_to_owner_issued, company_scoped_tables

    tables = company_scoped_tables(conn)
    _write_licence_state(tmp, OWNER_COMPANY_ID)
    _write_registry_company_id(tmp, OWNER_COMPANY_ID)    # identity HAS moved
    expected = _scoped_counts(conn, tables, LEGACY_COMPANY_ID)

    result = _migrate_rebind_company_id_to_owner_issued(conn)
    conn.commit()

    assert result['status'] == 'rebound', result
    assert _scoped_counts(conn, tables, OWNER_COMPANY_ID) == expected
    assert sum(_scoped_counts(conn, tables, LEGACY_COMPANY_ID).values()) == 0

    # ...and running it again changes nothing, which is what makes the
    # activation-time call safe to fire on every activation.
    again = _migrate_rebind_company_id_to_owner_issued(conn)
    conn.commit()
    assert again['status'] == 'already_bound', again
    assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    conn.close()


def test_activation_time_entry_point_is_the_same_function_the_migration_uses():
    """(c) in the design: an install that activates a licence LONG after it
    migrated must get rebound at that moment, not at some next-migration
    that may never come. `rebind_company_id_after_activation()` is the seam
    retail's app.py hands to `make_licensing_blueprint(on_activation_success=...)`,
    and it must open its own connection (the activation request has none)
    while doing exactly what the migration step does."""
    tmp, conn = _fresh_install()
    conn.close()

    import database.schema as sch
    _write_licence_state(tmp, OWNER_COMPANY_ID)
    _write_registry_company_id(tmp, OWNER_COMPANY_ID)

    result = sch.rebind_company_id_after_activation()
    assert result['status'] == 'rebound', result

    conn = sqlite3.connect(os.path.join(sch.SUBSYS_DIR, 'retail.db'))
    assert conn.execute('SELECT COUNT(*) FROM sales WHERE company_id=?',
                        (OWNER_COMPANY_ID,)).fetchone()[0] > 0
    assert conn.execute('SELECT COUNT(*) FROM sales WHERE company_id=?',
                        (LEGACY_COMPANY_ID,)).fetchone()[0] == 0
    conn.close()


def test_activation_time_entry_point_never_raises_into_the_activation_response():
    """A licence activation that succeeded at Owner must not be reported to
    the customer as a failure because a local bookkeeping rewrite hit a
    locked database. It returns a status dict, always."""
    tmp = _fresh_app_data('aura-retail-v14-act-safe-')
    import database.schema as sch
    sch.BASE_DIR = os.path.join(tmp, 'database')
    sch.SUBSYS_DIR = os.path.join(sch.BASE_DIR, 'subsystems')

    # No retail.db at all -- the harshest version of "something is wrong".
    result = sch.rebind_company_id_after_activation()
    assert isinstance(result, dict) and result['status'] in ('skipped', 'deferred', 'failed')
