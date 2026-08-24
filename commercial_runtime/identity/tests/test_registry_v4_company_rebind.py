"""Registry v4 -- identity-side `company_id` rebind from `md5(admin_email)` to
the Owner-issued `license_public_id` (ROADMAP.md's 2026-08-21 reservation,
launch-readiness Phase 5 prerequisite #1). See
commercial_runtime/identity/company_rebind.py and
docs/launch-readiness/phase5-prerequisites.md §1.

Structural sibling of products/retail/tests/retail_v14_company_rebind_
migration_test.py and retail_v14_migration_chain_wiring_test.py --
deliberately mirrored rather than reinvented (see company_rebind.py's own
module docstring for why the two exist on opposite sides of one boundary).
Every fixture/assertion shape below is the identity-side twin of a specific
retail v14 test; ONE new class of test is added at the bottom that retail's
suite does not need: proving `registry_tenant_ids()` tells a genuinely
multi-tenant registry apart from a converged single-tenant one, because
that distinction is what `products/retail/backend/app.py`'s boot-time
refuse-to-serve guard relies on to never punish a legitimate multi-tenant
install for a catastrophe that never happened to it.

Why this needs a real on-disk registry.db (not the in-memory fixtures the
rest of this tests/ directory uses for device_registry.py in isolation):
`company_id` is the tenant key registry.db is the source of truth for, and
a rebind that half-lands would make `mt_auth.create_session` populate
`session['company_id']` from a row nobody can find in a moment, or -- worse,
in production -- with retail.db already converged onto the intended value
while registry.db itself still disagrees. The tests below assert the three
things that distinguish a correct rebind from a plausible-looking one, the
same three retail's own v14 suite asserts:

  1. Every scoped table moves, in one transaction, with row counts verified
     before and after and NOTHING left behind on the old id.
  2. Re-running is a clean no-op.
  3. With no licence present it is a genuine no-op -- not an error -- and the
     schema version still advances, because v4 is about being ABLE to
     rebind, not about having done it.

Run (ONE FILE PER PYTEST PROCESS -- see products/run_all_tests.py):
    pytest commercial_runtime/identity/tests/test_registry_v4_company_rebind.py -v
"""
import json
import os
import shutil
import sqlite3
import tempfile
import uuid

import pytest

from commercial_runtime.identity import registry_db
from commercial_runtime.identity.company_rebind import (
    CompanyRebindError,
    _migrate_rebind_registry_company_id_to_owner_issued,
    company_scoped_tables,
    owner_issued_company_id,
    rebind_company_id,
    rebind_company_id_after_activation,
    registry_tenant_ids,
)

LEGACY_COMPANY_ID = 'd41d8cd98f00b204e9800998ecf8427e'   # a real md5 shape
SECOND_TENANT_ID = 'ffffffffffffffffffffffffffffffff'
OWNER_COMPANY_ID = '7c9e6679-7425-40de-944b-e07fc1f90ae7'  # a real Owner licence uuid

_TMP_DIRS = []


def teardown_module(module):
    for d in _TMP_DIRS:
        shutil.rmtree(d, ignore_errors=True)
    _TMP_DIRS.clear()


def _fresh_app_data(prefix):
    """Points registry_db's module-level path constants (cached at import
    time -- see company_rebind.py::_app_data_dir's docstring for why that is
    wrong for anything but a real single-boot process) AND `AURA_APP_DATA`
    itself at a fresh tmp dir, so `registry_db.init_registry_db()`/`get_conn()`
    and `company_rebind.py`'s own fresh-per-call resolution agree on the same
    on-disk location. Mirrors products/retail/tests/retail_v14_company_rebind_
    migration_test.py's `sch.BASE_DIR = ...` pattern for the identical reason.
    """
    tmp = tempfile.mkdtemp(prefix=prefix)
    _TMP_DIRS.append(tmp)
    os.environ['AURA_APP_DATA'] = tmp
    registry_db._db_dir = os.path.join(tmp, 'database')
    registry_db.DB_PATH = os.path.join(registry_db._db_dir, 'registry.db')
    return tmp


def _seed_company(conn, company_id, tag):
    """One representative row in EVERY scoped table registry.db ships today
    (discovered at runtime by company_scoped_tables, not enumerated here --
    if a future migration adds another company_id-bearing table, this
    fixture simply does not seed it, and test_rebind_moves_every_scoped_
    table_and_leaves_nothing_on_the_old_id's floor assertion is what would
    catch a rebind that then silently skipped it). `tag` keeps rows from
    two different companies seeded into the same database from colliding on
    UNIQUE columns (users.email, company_settings.company_id)."""
    now = '2026-08-24T00:00:00+00:00'
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role) "
        "VALUES (?,?,?,?,?,?)",
        (str(uuid.uuid4()), company_id, f'EMP-{tag}-1', f'admin-{tag}@test.local', 'x', 'admin'),
    )
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role) "
        "VALUES (?,?,?,?,?,?)",
        (str(uuid.uuid4()), company_id, f'EMP-{tag}-2', f'staff-{tag}@test.local', 'x', 'cashier'),
    )
    conn.execute(
        "INSERT INTO company_settings (id, company_id, country) VALUES (?,?,?)",
        (str(uuid.uuid4()), company_id, 'JO'),
    )
    conn.execute(
        "INSERT INTO company_modules (id, company_id, module_code) VALUES (?,?,?)",
        (str(uuid.uuid4()), company_id, 'RETAIL'),
    )
    conn.execute(
        "INSERT INTO audit_logs (id, company_id, user_id, action, entity_type, entity_id) "
        "VALUES (?,?,?,?,?,?)",
        (str(uuid.uuid4()), company_id, None, 'created', 'company', tag),
    )
    conn.execute(
        "INSERT INTO secure_links (id, company_id, token_hash, email_target, expires_at) "
        "VALUES (?,?,?,?,?)",
        (str(uuid.uuid4()), company_id, f'hash-{tag}', f'invite-{tag}@test.local', now),
    )
    conn.execute(
        "INSERT INTO devices (id, company_id, is_admin_device, status, first_seen_at) "
        "VALUES (?,?,?,?,?)",
        (str(uuid.uuid4()), company_id, 1, 'active', now),
    )
    conn.commit()


def _fresh_registry(prefix='aura-registry-v4-'):
    """A real registry.db (full v0->v4 chain via the real
    init_registry_db()), then seeded exactly as if a real install had
    onboarded under the locally-derived legacy company_id -- i.e. exactly
    what every install looks like before Owner ever issues it a tenant
    key."""
    tmp = _fresh_app_data(prefix)
    registry_db.init_registry_db()

    conn = sqlite3.connect(registry_db.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    _seed_company(conn, LEGACY_COMPANY_ID, 'legacy')
    return tmp, conn


def _scoped_counts(conn, tables, company_id):
    return {
        t: conn.execute(f'SELECT COUNT(*) FROM "{t}" WHERE company_id=?', (company_id,)).fetchone()[0]
        for t in tables
    }


def _write_licence_state(app_data, license_public_id, *, envelope=True):
    """Writes the licensing_state row `owner_issued_company_id()` reads.
    Plain sqlite3/json, mirroring products/retail/tests/retail_v14_company_
    rebind_migration_test.py's helper of the same name -- and the reader
    itself, for the same reason: this must stay importable on Android, where
    importing licensing_contracts pulls in `cryptography` at module scope."""
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
         '2026-08-24T00:00:00'),
    )
    conn.commit()
    conn.close()


# ── 1. the rebind itself ────────────────────────────────────────────────────

def test_rebind_moves_every_scoped_table_and_leaves_nothing_on_the_old_id():
    _tmp, conn = _fresh_registry()
    tables = company_scoped_tables(conn)
    # Discovered at runtime, not hardcoded -- assert a floor, not an exact
    # count, so a future additive migration that adds another scoped table
    # does not fail this test for the wrong reason (same reasoning as
    # retail's own v14 test for the identical assertion shape).
    assert len(tables) >= 6, f'only found {len(tables)} scoped tables: {tables}'
    for expected in ('users', 'company_settings', 'company_modules', 'audit_logs',
                      'secure_links', 'devices'):
        assert expected in tables, f'{expected} is tenant-scoped but was not discovered'
    # user_devices deliberately absent: it scopes entirely through user_id,
    # the same shape retail's line-item tables use through their parent row.
    assert 'user_devices' not in tables

    before_total = {t: conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables}
    before_legacy = _scoped_counts(conn, tables, LEGACY_COMPANY_ID)
    assert sum(before_legacy.values()) > 0, 'fixture has no scoped rows to move'

    result = rebind_company_id(conn, OWNER_COMPANY_ID)
    conn.commit()

    assert result['status'] == 'rebound', result
    assert result['old_company_id'] == LEGACY_COMPANY_ID
    assert result['new_company_id'] == OWNER_COMPANY_ID
    assert result['rows'] == sum(before_legacy.values())

    after_total = {t: conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables}
    assert after_total == before_total, 'the rebind changed a row count'

    after_new = _scoped_counts(conn, tables, OWNER_COMPANY_ID)
    assert after_new == before_legacy, 'not every row landed on the new id'

    stranded = _scoped_counts(conn, tables, LEGACY_COMPANY_ID)
    assert sum(stranded.values()) == 0, f'rows left on the old tenant key: {stranded}'
    conn.close()


def test_rebind_is_idempotent():
    _tmp, conn = _fresh_registry('aura-registry-v4-idem-')
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


def test_rebind_finishes_a_registry_that_holds_rows_under_both_ids():
    """The interrupted-rebind state (design doc item (c), the self-healing
    half): rebind_company_id derives the old id from registry.db's OWN rows
    precisely so a crash that moved `users` but nothing else converges on
    the next call instead of deadlocking on "which of these two is real?"."""
    _tmp, conn = _fresh_registry('aura-registry-v4-half-')
    tables = company_scoped_tables(conn)
    conn.execute('UPDATE users SET company_id=?', (OWNER_COMPANY_ID,))
    conn.commit()

    result = rebind_company_id(conn, OWNER_COMPANY_ID)
    conn.commit()

    assert result['status'] == 'rebound', result
    assert result['old_company_id'] == LEGACY_COMPANY_ID
    assert sum(_scoped_counts(conn, tables, LEGACY_COMPANY_ID).values()) == 0
    conn.close()


def test_rebind_rolls_back_completely_when_one_table_fails_mid_flight():
    """(c), the hard-crash half: one transaction, not six. If the rewrite
    cannot complete, the tenant key must be left ENTIRELY on the old value --
    three tables moved and three not is the exact silent-invisibility state
    this migration exists to avoid. Same FlakyConnection technique as
    products/retail/tests/retail_v14_company_rebind_migration_test.py's test
    of the same name: `sqlite3.Connection.execute` is read-only on the C
    type, so the failure is injected via a Connection subclass rather than a
    monkeypatched attribute -- the same connection object production code
    would get, just one that gives up partway through the rewrite.
    """
    _tmp, conn = _fresh_registry('aura-registry-v4-flaky-')
    db_path = conn.execute('PRAGMA database_list').fetchone()[2]
    tables = company_scoped_tables(conn)
    before = _scoped_counts(conn, tables, LEGACY_COMPANY_ID)
    conn.close()

    class FlakyConnection(sqlite3.Connection):
        updates = 0

        def execute(self, sql, *args):
            if sql.lstrip().upper().startswith('UPDATE'):
                FlakyConnection.updates += 1
                if FlakyConnection.updates > 2:
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
    assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    conn.close()


def test_rebind_refuses_rather_than_merging_two_unrelated_tenants():
    """CLAUDE.md: "one install *can* host more than one company". Blanket-
    moving every row onto one licence's id would merge two tenants' books
    into one -- unrecoverable, and it would look like a successful
    migration. When the old id cannot be identified unambiguously this
    refuses and changes nothing."""
    _tmp, conn = _fresh_registry('aura-registry-v4-ambig-')
    tables = company_scoped_tables(conn)
    _seed_company(conn, SECOND_TENANT_ID, 'second')
    before = _scoped_counts(conn, tables, LEGACY_COMPANY_ID)

    with pytest.raises(CompanyRebindError):
        rebind_company_id(conn, OWNER_COMPANY_ID)

    assert _scoped_counts(conn, tables, LEGACY_COMPANY_ID) == before, 'refusal was not clean'
    assert conn.execute('SELECT COUNT(*) FROM users WHERE company_id=?',
                        (SECOND_TENANT_ID,)).fetchone()[0] > 0
    conn.close()


def test_rebind_with_an_explicit_old_id_moves_only_that_tenant():
    """The multi-tenant escape hatch: told exactly which tenant to move, it
    moves that one and leaves the other alone."""
    _tmp, conn = _fresh_registry('aura-registry-v4-explicit-')
    _seed_company(conn, SECOND_TENANT_ID, 'second')
    others = conn.execute('SELECT COUNT(*) FROM users WHERE company_id=?',
                          (SECOND_TENANT_ID,)).fetchone()[0]

    result = rebind_company_id(conn, OWNER_COMPANY_ID, old_company_id=LEGACY_COMPANY_ID)
    conn.commit()

    assert result['status'] == 'rebound'
    assert conn.execute('SELECT COUNT(*) FROM users WHERE company_id=?',
                        (SECOND_TENANT_ID,)).fetchone()[0] == others
    assert conn.execute('SELECT COUNT(*) FROM users WHERE company_id=?',
                        (OWNER_COMPANY_ID,)).fetchone()[0] > 0
    conn.close()


def test_rebind_is_a_no_op_not_an_error_when_no_owner_issued_id_exists():
    """Design item (b): licensing is OFF by default, so the common case is
    an install that migrates long before it ever activates."""
    _tmp, conn = _fresh_registry('aura-registry-v4-noop-')
    tables = company_scoped_tables(conn)
    before = _scoped_counts(conn, tables, LEGACY_COMPANY_ID)

    for absent in (None, '', '   '):
        result = rebind_company_id(conn, absent)
        assert result['status'] == 'skipped', result
        assert result['rows'] == 0

    assert _scoped_counts(conn, tables, LEGACY_COMPANY_ID) == before
    conn.close()


# ── 2. where the Owner-issued id comes from ─────────────────────────────────

def test_owner_issued_company_id_reads_license_public_id_from_the_stored_assertion():
    tmp = _fresh_app_data('aura-registry-v4-lic-')
    assert owner_issued_company_id(tmp) is None, 'unlicensed install must report nothing'

    _write_licence_state(tmp, OWNER_COMPANY_ID)
    assert owner_issued_company_id(tmp) == OWNER_COMPANY_ID


def test_owner_issued_company_id_never_raises_on_a_damaged_or_absent_licence_db():
    tmp = _fresh_app_data('aura-registry-v4-lic-bad-')
    _write_licence_state(tmp, OWNER_COMPANY_ID, envelope=False)
    assert owner_issued_company_id(tmp) is None

    db_dir = os.path.join(tmp, 'database', 'subsystems')
    with open(os.path.join(db_dir, 'licensing.db'), 'wb') as f:
        f.write(b'this is not a sqlite database')
    assert owner_issued_company_id(tmp) is None


# ── 3. registry_tenant_ids: telling "converged" apart from "genuinely multi-tenant" ─

def test_registry_tenant_ids_reports_exactly_one_value_for_a_converged_single_tenant_registry():
    _tmp, conn = _fresh_registry('aura-registry-v4-tenants-one-')
    assert registry_tenant_ids(conn) == {LEGACY_COMPANY_ID}
    rebind_company_id(conn, OWNER_COMPANY_ID)
    conn.commit()
    assert registry_tenant_ids(conn) == {OWNER_COMPANY_ID}
    conn.close()


def test_registry_tenant_ids_reports_every_value_for_a_genuinely_multi_tenant_registry():
    """This is the exact set products/retail/backend/app.py's boot-time
    refuse-to-serve guard reads to tell a legitimate multi-tenant install
    (CLAUDE.md: "one install *can* host more than one company") apart from a
    single-tenant install that has simply not converged yet -- see
    _converge_and_refuse_to_serve_if_stuck's docstring."""
    _tmp, conn = _fresh_registry('aura-registry-v4-tenants-many-')
    _seed_company(conn, SECOND_TENANT_ID, 'second')
    assert registry_tenant_ids(conn) == {LEGACY_COMPANY_ID, SECOND_TENANT_ID}
    conn.close()


# ── 4. the migration step ───────────────────────────────────────────────────

def test_v4_migration_is_a_no_op_and_the_version_still_advances_without_a_licence():
    tmp = _fresh_app_data('aura-registry-v4-migrate-unlicensed-')
    registry_db.init_registry_db()

    conn = sqlite3.connect(registry_db.DB_PATH)
    assert conn.execute('PRAGMA user_version').fetchone()[0] == registry_db.REGISTRY_SCHEMA_VERSION
    assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    conn.close()


def test_v4_migration_rebinds_when_a_licence_is_already_present_at_migration_time():
    """Unlike retail's v14 (which must WAIT for identity), identity's own
    migration step leads -- it acts the moment it has an unambiguous old id
    and a real Owner-issued one, with nothing to defer to."""
    tmp, conn = _fresh_registry('aura-registry-v4-migrate-licensed-')
    tables = company_scoped_tables(conn)
    _write_licence_state(tmp, OWNER_COMPANY_ID)
    expected = _scoped_counts(conn, tables, LEGACY_COMPANY_ID)

    result = _migrate_rebind_registry_company_id_to_owner_issued(conn)
    conn.commit()

    assert result['status'] == 'rebound', result
    assert _scoped_counts(conn, tables, OWNER_COMPANY_ID) == expected
    assert sum(_scoped_counts(conn, tables, LEGACY_COMPANY_ID).values()) == 0

    again = _migrate_rebind_registry_company_id_to_owner_issued(conn)
    conn.commit()
    assert again['status'] == 'already_bound', again
    conn.close()


def test_a_multi_tenant_registry_still_boots_and_still_advances_user_version():
    """Design item (d), the boot-level consequence rather than the isolated
    step call: a genuinely pre-v4, two-tenant registry.db still reaches
    REGISTRY_SCHEMA_VERSION through the REAL init_registry_db() path, because
    the refusal is caught inside the v4 step itself -- the whole migration
    chain completes normally around it. Mirrors products/retail/tests/
    retail_v14_migration_chain_wiring_test.py's test of the same name."""
    tmp = _fresh_app_data('aura-registry-v4-boot-refuse-')

    # A REAL pre-v4 registry (v0->v3 chain only), then two tenants seeded
    # directly -- registry_db.init_registry_db() itself is what has to prove
    # it still reaches v4 despite the refusal, not a hand-rolled substitute.
    real_version = registry_db.REGISTRY_SCHEMA_VERSION
    registry_db.REGISTRY_SCHEMA_VERSION = 3
    try:
        registry_db.init_registry_db()
    finally:
        registry_db.REGISTRY_SCHEMA_VERSION = real_version

    conn = sqlite3.connect(registry_db.DB_PATH)
    conn.row_factory = sqlite3.Row
    _seed_company(conn, LEGACY_COMPANY_ID, 'legacy')
    _seed_company(conn, SECOND_TENANT_ID, 'second')
    conn.close()
    _write_licence_state(tmp, OWNER_COMPANY_ID)

    before = {}
    conn = sqlite3.connect(registry_db.DB_PATH)
    for cid in (LEGACY_COMPANY_ID, SECOND_TENANT_ID):
        before[cid] = conn.execute('SELECT COUNT(*) FROM users WHERE company_id=?', (cid,)).fetchone()[0]
    conn.close()

    registry_db.init_registry_db()  # the real boot path -- must not raise

    conn = sqlite3.connect(registry_db.DB_PATH)
    try:
        assert conn.execute('PRAGMA user_version').fetchone()[0] == registry_db.REGISTRY_SCHEMA_VERSION, (
            'user_version did not advance past a refusal that can never resolve itself on its own -- '
            'this install can never take another migration'
        )
        for cid, count in before.items():
            assert conn.execute(
                'SELECT COUNT(*) FROM users WHERE company_id=?', (cid,)
            ).fetchone()[0] == count, f'{cid} tenant keys were touched despite the refusal'
        assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    finally:
        conn.close()


def test_the_multi_tenant_refusal_survives_repeated_retries_and_recovers_once_resolved():
    """Design item (d)'s "run it three times" requirement: repeatedly
    retrying rebind_company_id_after_activation() (the seam every subsequent
    boot and every activation retry actually goes through, per
    products/retail/backend/app.py::_converge_and_refuse_to_serve_if_stuck)
    against a genuinely irreconcilable registry must never corrupt anything,
    never merge the two tenants, and never wedge the ability to converge --
    the refusal has to be resolvable the moment a human actually fixes the
    ambiguity (removes/reassigns the extra tenant), not just theoretically."""
    tmp, conn = _fresh_registry('aura-registry-v4-retry-')
    conn.close()
    _write_licence_state(tmp, OWNER_COMPANY_ID)

    # registry_tenant_ids() derivation needs the licence's own id to be
    # ABSENT from the present set for _fresh_registry's LEGACY_COMPANY_ID to
    # be picked up as "the" tenant to move; seed a genuine second tenant so
    # the ambiguity is real, not incidental.
    conn = sqlite3.connect(registry_db.DB_PATH)
    conn.row_factory = sqlite3.Row
    _seed_company(conn, SECOND_TENANT_ID, 'second')
    conn.close()

    for attempt in range(3):
        result = rebind_company_id_after_activation()
        assert result['status'] == 'failed', (attempt, result)
        assert 'tenant keys' in result['reason'], (attempt, result)

    conn = sqlite3.connect(registry_db.DB_PATH)
    conn.row_factory = sqlite3.Row
    tables = company_scoped_tables(conn)
    assert _scoped_counts(conn, tables, LEGACY_COMPANY_ID) != {t: 0 for t in tables}, \
        'three failed retries somehow moved the legacy tenant anyway'
    assert _scoped_counts(conn, tables, SECOND_TENANT_ID) != {t: 0 for t in tables}, \
        'three failed retries somehow moved the second tenant anyway'
    assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'

    # The ambiguity resolves the ordinary way: the second tenant's rows are
    # removed (a real operator action -- e.g. that company was deleted or
    # never should have shared this install). The VERY NEXT call converges.
    for table in tables:
        conn.execute(f'DELETE FROM "{table}" WHERE company_id=?', (SECOND_TENANT_ID,))
    conn.commit()
    conn.close()

    result = rebind_company_id_after_activation()
    assert result['status'] == 'rebound', result

    conn = sqlite3.connect(registry_db.DB_PATH)
    assert conn.execute('SELECT COUNT(*) FROM users WHERE company_id=?',
                        (OWNER_COMPANY_ID,)).fetchone()[0] > 0
    conn.close()


def test_the_v4_step_catches_the_multi_tenant_refusal_instead_of_raising():
    """Design item (d), the "does not wedge the version marker" half.
    `CompanyRebindError` is caught here and only here -- see
    _migrate_rebind_registry_company_id_to_owner_issued's docstring for why
    raising it out of a migration would freeze user_version forever."""
    tmp, conn = _fresh_registry('aura-registry-v4-migrate-refuse-')
    _seed_company(conn, SECOND_TENANT_ID, 'second')
    _write_licence_state(tmp, OWNER_COMPANY_ID)

    result = _migrate_rebind_registry_company_id_to_owner_issued(conn)

    assert isinstance(result, dict), 'the migration step must report, never raise'
    assert result['status'] == 'deferred', result
    assert result['rows'] == 0
    assert 'tenant keys' in result['reason'], (
        f'the refusal reason was lost on the way out: {result["reason"]!r}'
    )
    conn.close()


# ── 5. activation-time entry point ──────────────────────────────────────────

def test_activation_time_entry_point_rebinds_registry():
    tmp, conn = _fresh_registry('aura-registry-v4-activate-')
    conn.close()
    _write_licence_state(tmp, OWNER_COMPANY_ID)

    result = rebind_company_id_after_activation()
    assert result['status'] == 'rebound', result

    conn = sqlite3.connect(registry_db.DB_PATH)
    assert conn.execute('SELECT COUNT(*) FROM users WHERE company_id=?',
                        (OWNER_COMPANY_ID,)).fetchone()[0] > 0
    assert conn.execute('SELECT COUNT(*) FROM users WHERE company_id=?',
                        (LEGACY_COMPANY_ID,)).fetchone()[0] == 0
    conn.close()


def test_activation_time_entry_point_never_raises_into_the_activation_response():
    """A licence activation that succeeded at Owner must not be reported to
    the customer as a failure because a local bookkeeping rewrite hit a
    locked database. It returns a status dict, always."""
    tmp = _fresh_app_data('aura-registry-v4-activate-safe-')
    # No registry.db at all -- the harshest version of "something is wrong".
    result = rebind_company_id_after_activation()
    assert isinstance(result, dict) and result['status'] in ('skipped', 'deferred', 'failed')


# ── 6. registry v4 wired into the real migration chain ─────────────────────

def test_the_v4_rebind_step_actually_runs_inside_the_migration_chain():
    """Asserts the CALL HAPPENED, not that a rebind happened -- the
    unlicensed install (the common case) makes "the step ran and did
    nothing" and "the step was deleted" produce the identical outcome, so
    only observing the call itself can tell them apart. Same reasoning as
    products/retail/tests/retail_v14_migration_chain_wiring_test.py's test
    of the same name."""
    tmp = _fresh_app_data('aura-registry-v4-wiring-call-')

    calls = []
    from commercial_runtime.identity import company_rebind as _company_rebind_module
    real_v4 = _company_rebind_module._migrate_rebind_registry_company_id_to_owner_issued

    def _spy(conn):
        calls.append('v4')
        return real_v4(conn)

    _company_rebind_module._migrate_rebind_registry_company_id_to_owner_issued = _spy
    try:
        registry_db.init_registry_db()
    finally:
        _company_rebind_module._migrate_rebind_registry_company_id_to_owner_issued = real_v4

    assert calls == ['v4'], (
        'the v4 rebind step is not called from _migrate_registry_schema -- an install that '
        'later activates a licence would migrate straight past the only step that can '
        'move it onto its Owner-issued tenant key at migration time'
    )
