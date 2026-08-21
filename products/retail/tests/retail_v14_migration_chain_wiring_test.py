"""
Aura Retail -- schema v14: is the migration STEP actually wired into the
chain, in the right place, with its refusal caught? See
database/schema.py::_migrate_retail_schema and
::_migrate_rebind_company_id_to_owner_issued.

The existing v14 file tests `rebind_company_id`, `_rebind_to_owner_issued`
and `_migrate_rebind_company_id_to_owner_issued` thoroughly -- by CALLING
each of them directly. Nothing tested the wiring, and three separate
mutations proved it:

  - Deleting `_migrate_rebind_company_id_to_owner_issued(conn)` from
    `_migrate_retail_schema` entirely                         -> 25 passed.
  - Moving the v14 step BEFORE the v13 step                   -> 25 passed,
    despite a comment in `_migrate_retail_schema` saying in as many words
    that the order is load-bearing.
  - Deleting the `except CompanyRebindError` from the step    -> 14 passed,
    the guard whose own docstring says raising there "would leave a
    multi-tenant install unable to advance its schema version ever again".

Each is a different kind of hole, so each gets a different kind of test:

  1. The step RUNS. Asserted by observing the call, not by observing a
     rebind -- because the common install has no licence, so the step's
     visible outcome is "nothing happened", which is also exactly what a
     deleted call looks like. An outcome assertion here would be the classic
     test that cannot fail.
  2. The step runs AFTER v13. Asserted by reading the live database at the
     moment the step is entered, on a genuinely pre-v13 fixture -- so it
     checks the property the order exists for (the rows carry their wire
     identity before anything rewrites their tenant key), not merely two
     list positions.
  3. The refusal is CAUGHT. Asserted end-to-end through `init_retail()` on a
     real two-tenant database, because the thing being protected is
     `user_version` advancing, and only the boot path can show that.

Run:
    pytest products/retail/tests/retail_v14_migration_chain_wiring_test.py -v
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

LEGACY_COMPANY_ID = 'd41d8cd98f00b204e9800998ecf8427e'
SECOND_TENANT_ID = 'ffffffffffffffffffffffffffffffff'
OWNER_COMPANY_ID = '7c9e6679-7425-40de-944b-e07fc1f90ae7'

_TMP_DIRS = []


def teardown_module(module):
    for d in _TMP_DIRS:
        shutil.rmtree(d, ignore_errors=True)
    _TMP_DIRS.clear()


def _point_schema_at_a_fresh_app_data(prefix):
    tmp = tempfile.mkdtemp(prefix=prefix)
    _TMP_DIRS.append(tmp)
    os.environ['AURA_APP_DATA'] = tmp

    import database.schema as sch
    sch.BASE_DIR = os.path.join(tmp, 'database')
    sch.SUBSYS_DIR = os.path.join(sch.BASE_DIR, 'subsystems')
    return tmp, sch


def _build_v12_install(prefix='aura-retail-v14-chain-'):
    """A REAL schema-v12 retail.db -- genuine `init_retail()` with the v13/v14
    steps stubbed out and RETAIL_SCHEMA_VERSION pinned back to 12.

    Pre-v13 matters here specifically: the order assertion below reads
    whether `sales.uid` exists at the moment the v14 step is entered, and on
    a database that already went through v13 that column is there no matter
    what order the chain runs in. A fixture at the current version would
    make the order test pass unconditionally -- it would be testing its own
    setup.
    """
    tmp, sch = _point_schema_at_a_fresh_app_data(prefix)

    real_version = sch.RETAIL_SCHEMA_VERSION
    real_v13 = sch._migrate_add_identity_and_attribution_columns
    real_v14 = sch._migrate_rebind_company_id_to_owner_issued
    sch.RETAIL_SCHEMA_VERSION = 12
    sch._migrate_add_identity_and_attribution_columns = lambda conn: None
    sch._migrate_rebind_company_id_to_owner_issued = lambda conn: None
    try:
        sch.init_retail()
    finally:
        sch.RETAIL_SCHEMA_VERSION = real_version
        sch._migrate_add_identity_and_attribution_columns = real_v13
        sch._migrate_rebind_company_id_to_owner_issued = real_v14

    db_path = os.path.join(sch.SUBSYS_DIR, 'retail.db')
    probe = sqlite3.connect(db_path)
    try:
        assert probe.execute('PRAGMA user_version').fetchone()[0] == 12
        cols = {r[1] for r in probe.execute('PRAGMA table_info(sales)')}
        assert 'uid' not in cols, 'fixture is not really pre-v13'
    finally:
        probe.close()
    return tmp, sch, db_path


def _columns(conn, table):
    return {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")').fetchall()}


def _write_licence_state(app_data, license_public_id):
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
         json.dumps({'payload': payload}), '2026-08-21T00:00:00'),
    )
    conn.commit()
    conn.close()


def _write_registry_company_id(app_data, company_id):
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


# ── 1. the step is in the chain at all ──────────────────────────────────────

def test_the_v14_rebind_step_actually_runs_inside_the_migration_chain():
    """Asserts the CALL HAPPENED, not that a rebind happened.

    This distinction is the whole test. On an unlicensed install -- the
    overwhelmingly common one, since licensing is OFF by default in this
    product -- the v14 step's entire visible effect is that nothing changes.
    "Nothing changed" is also precisely what deleting the call produces, so
    any assertion phrased as an outcome is unfalsifiable here.

    `_migrate_retail_schema` resolves its steps as module globals at call
    time, so swapping the attribute is enough to observe the real chain
    rather than a re-implementation of it.
    """
    _tmp, sch, db_path = _build_v12_install()

    calls = []
    real_v14 = sch._migrate_rebind_company_id_to_owner_issued

    def _spy(conn):
        calls.append('v14')
        return real_v14(conn)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    sch._migrate_rebind_company_id_to_owner_issued = _spy
    try:
        sch._migrate_retail_schema(conn)
        conn.commit()
    finally:
        sch._migrate_rebind_company_id_to_owner_issued = real_v14
        conn.close()

    assert calls == ['v14'], (
        'the v14 rebind step is not called by _migrate_retail_schema -- an install that '
        'later activates a licence would migrate straight past the only step that can '
        'move it onto its Owner-issued tenant key'
    )


def test_the_v13_identity_step_actually_runs_inside_the_migration_chain():
    """Its sibling. v13 is currently covered only through direct calls and
    through a fresh install's end state; the same deletion would be just as
    invisible, and v13 is the step that mints the wire identity everything
    downstream keys on."""
    _tmp, sch, db_path = _build_v12_install('aura-retail-v13-chain-')

    calls = []
    real_v13 = sch._migrate_add_identity_and_attribution_columns

    def _spy(conn):
        calls.append('v13')
        return real_v13(conn)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    sch._migrate_add_identity_and_attribution_columns = _spy
    try:
        sch._migrate_retail_schema(conn)
        conn.commit()
    finally:
        sch._migrate_add_identity_and_attribution_columns = real_v13
        conn.close()

    assert calls == ['v13']


# ── 2. the order the chain's own comment calls load-bearing ─────────────────

def test_v13_has_already_applied_by_the_time_the_v14_rebind_step_is_entered():
    """`_migrate_retail_schema` says of the v14 step: "it must stay after v13
    specifically -- not merely by the 'new steps go last' convention... running
    it BEFORE v13 would mean the rows it touches do not yet carry the `uid`
    that identifies them on the wire, so an interrupted rebind could not be
    reconciled against anything afterwards."

    That claim was unenforced. This asserts it the way it is stated -- by
    looking at the live database at the instant the v14 step is entered,
    starting from a database that genuinely has no `uid` column yet. Two
    recorded list positions would prove the same thing today and stop
    proving it the moment the rebind moves behind a helper.
    """
    _tmp, sch, db_path = _build_v12_install('aura-retail-v14-order-')

    observed = {}
    real_v13 = sch._migrate_add_identity_and_attribution_columns
    real_v14 = sch._migrate_rebind_company_id_to_owner_issued

    def _spy_v13(conn):
        observed.setdefault('order', []).append('v13')
        return real_v13(conn)

    def _spy_v14(conn):
        observed.setdefault('order', []).append('v14')
        observed['uid_tables_at_v14'] = {
            table: ('uid' in _columns(conn, table))
            for table in ('sales', 'sale_items', 'returns', 'return_items',
                          'payments', 'branches', 'inventory_movements')
        }
        return real_v14(conn)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    sch._migrate_add_identity_and_attribution_columns = _spy_v13
    sch._migrate_rebind_company_id_to_owner_issued = _spy_v14
    try:
        sch._migrate_retail_schema(conn)
        conn.commit()
    finally:
        sch._migrate_add_identity_and_attribution_columns = real_v13
        sch._migrate_rebind_company_id_to_owner_issued = real_v14
        conn.close()

    assert 'uid_tables_at_v14' in observed, 'the v14 step never ran, so order proves nothing'
    # The property first, the ordering second: this is the reason the order
    # exists, and it keeps holding if the two steps are ever restructured
    # into something that is not a flat pair of calls.
    missing = [t for t, present in observed['uid_tables_at_v14'].items() if not present]
    assert not missing, (
        f'the v14 rebind was entered while {missing} still had no `uid` column. It is about '
        'to rewrite the tenant key on rows that carry no wire identity yet, so an '
        'interrupted rebind could not be reconciled against anything afterwards.'
    )
    assert observed.get('order') == ['v13', 'v14'], (
        f'migration steps ran in the order {observed.get("order")}; the rebind must run '
        'after the identity columns exist'
    )


# ── 3. the refusal is caught, and user_version still moves ──────────────────

def _two_tenant_licensed_install(prefix):
    """A licensed install whose retail.db holds rows under TWO tenant keys --
    the state CLAUDE.md says is legitimate ("one install *can* host more than
    one company") and the one `rebind_company_id` refuses to act on."""
    tmp, sch, db_path = _build_v12_install(prefix)

    conn = sqlite3.connect(db_path)
    for table in sch.company_scoped_tables(conn):
        conn.execute(f'UPDATE "{table}" SET company_id=?', (LEGACY_COMPANY_ID,))
    conn.execute('UPDATE sales SET company_id=?', (SECOND_TENANT_ID,))
    conn.commit()
    conn.close()

    # A licence exists AND the identity layer has already adopted its id, so
    # the rebind gets all the way past `_rebind_to_owner_issued`'s deferral
    # guard and reaches the ambiguity refusal. Without both of these the
    # step returns 'deferred' long before CompanyRebindError is ever raised,
    # and this test would pass on a build with no `except` clause at all.
    _write_licence_state(tmp, OWNER_COMPANY_ID)
    _write_registry_company_id(tmp, OWNER_COMPANY_ID)
    return tmp, sch, db_path


def test_the_two_tenant_refusal_is_reached_at_all_by_this_fixture():
    """Fixture self-check, and the reason the test below is not vacuous.

    `_rebind_to_owner_issued` returns 'deferred' without ever calling
    `rebind_company_id` when the identity layer has not adopted the
    Owner-issued id -- and 'deferred' is also what the caught refusal
    returns. So a fixture that got the licence/registry setup subtly wrong
    would produce the expected status via a path that never raises
    CompanyRebindError, and removing the `except` would not fail anything.
    This asserts the raise really is on the table.
    """
    _tmp, sch, db_path = _two_tenant_licensed_install('aura-retail-v14-refuse-probe-')

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        with pytest.raises(sch.CompanyRebindError):
            sch._rebind_to_owner_issued(conn, OWNER_COMPANY_ID)
    finally:
        conn.close()


def test_the_v14_step_catches_the_multi_tenant_refusal_instead_of_raising():
    _tmp, sch, db_path = _two_tenant_licensed_install('aura-retail-v14-refuse-step-')

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        result = sch._migrate_rebind_company_id_to_owner_issued(conn)
    finally:
        conn.close()

    assert isinstance(result, dict), 'the migration step must report, never raise'
    assert result['status'] == 'deferred', result
    assert result['rows'] == 0
    assert 'tenant keys' in result['reason'], (
        f'the refusal reason was lost on the way out: {result["reason"]!r}'
    )


def test_a_multi_tenant_install_still_boots_and_still_advances_user_version():
    """What the `except CompanyRebindError` is actually protecting, stated as
    the consequence rather than the mechanism.

    The refusal itself is correct and permanent -- retrying cannot resolve
    which of two tenants a licence belongs to. Letting it out of the
    migration would therefore freeze `user_version` forever on an install
    that has done nothing wrong, blocking every future migration behind a
    condition that will never clear on its own.
    """
    _tmp, sch, db_path = _two_tenant_licensed_install('aura-retail-v14-refuse-boot-')

    before = {}
    conn = sqlite3.connect(db_path)
    for table in ('sales', 'products', 'customers'):
        before[table] = conn.execute(
            f'SELECT company_id, COUNT(*) FROM "{table}" GROUP BY company_id'
        ).fetchall()
    conn.close()

    sch.init_retail()

    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute('PRAGMA user_version').fetchone()[0] == sch.RETAIL_SCHEMA_VERSION, (
            'user_version did not advance past a refusal that can never resolve itself; '
            'this install can never take another migration'
        )
        for table, snapshot in before.items():
            assert conn.execute(
                f'SELECT company_id, COUNT(*) FROM "{table}" GROUP BY company_id'
            ).fetchall() == snapshot, f'{table} tenant keys were touched despite the refusal'
        assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    finally:
        conn.close()
