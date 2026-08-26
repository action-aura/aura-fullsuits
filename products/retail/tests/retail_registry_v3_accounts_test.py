"""
Aura Retail -- registry.db v3: the multi-device account model.

Covers docs/launch-readiness/multi-device-design.md §6's "registry v3" bullet
as implemented by commercial_runtime/identity/account_schema.py and
commercial_runtime/identity/user_accounts.py:

  - `users` gains `uid` (backfilled uuid4, unique), `pin_hash`,
    `row_version`, `updated_at_utc`, `deleted_at_utc`;
  - `role` widens {admin, employee} -> {admin, manager, cashier}, existing
    rows mapped admin->admin and employee->cashier;
  - capability rows for the eight design §3 codes are seeded into the
    EXISTING `user_permissions` table;
  - PINs are attribution, never authorization, and are stored with the same
    KDF as passwords.

registry.db is a SEPARATE database from retail.db with its OWN version
counter -- this file is entirely about the registry one (REGISTRY_SCHEMA_VERSION
in registry_db.py, at least 3 as of this wave; a later wave's launch-readiness
Phase 5 prerequisite #1 added v4 on top -- see commercial_runtime/identity/
company_rebind.py -- and this file's own version-floor assertion below is
deliberately written not to pin an exact number for that reason). retail.db's
RETAIL_SCHEMA_VERSION is untouched by this phase.

Structure follows commercial_runtime/tests/registry_migration_test.py: most
tests run the migration against a synthetic, product-independent fixture
database standing in for a real v2 registry.db, so the assertions are about
the migration itself rather than about whichever rows a booted product
happens to have. Two tests at the end check the real wiring (registry_db
actually reaching v3 on a live database).

Run:
    pytest products/retail/tests/retail_registry_v3_accounts_test.py -v
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_registry_v3_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)
os.environ.pop("AURA_RETAIL_DEMO_MODE", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402
seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from commercial_runtime.identity import registry_db  # noqa: E402
from commercial_runtime.identity import user_accounts  # noqa: E402
from commercial_runtime.identity.account_schema import apply_account_schema  # noqa: E402
from commercial_runtime.security.migration_safety import ensure_schema_version  # noqa: E402
from commercial_runtime.security.passwords import hash_password, verify_password  # noqa: E402


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


# ── Synthetic v2 fixture ─────────────────────────────────────────────────────

def _make_v2_fixture(tmp_path):
    """A stand-in for a real registry.db at REGISTRY_SCHEMA_VERSION 2: the
    `users` and `user_permissions` shapes registry_db.init_registry_db()
    creates, already carrying an admin, two employees, and one legacy
    subsystem-level permission grant of the kind `mt_require_subsystem`
    reads today."""
    db_path = os.path.join(str(tmp_path), 'registry.db')
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript('''
        CREATE TABLE users (
            id              TEXT PRIMARY KEY,
            company_id      TEXT NOT NULL,
            employee_id     TEXT NOT NULL,
            email           TEXT UNIQUE NOT NULL,
            password_hash   TEXT NOT NULL,
            role            TEXT DEFAULT 'employee',
            status          TEXT DEFAULT 'active',
            require_password_change INTEGER DEFAULT 1,
            session_version INTEGER DEFAULT 1,
            language        TEXT DEFAULT 'en',
            clinic_role     TEXT DEFAULT '',
            failed_login_count INTEGER DEFAULT 0,
            locked_until    TEXT,
            created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
            email_verified_at TEXT,
            UNIQUE(company_id, employee_id)
        );
        CREATE TABLE user_permissions (
            id              TEXT PRIMARY KEY,
            user_id         TEXT NOT NULL,
            subsystem       TEXT NOT NULL,
            access_level    TEXT DEFAULT 'none',
            UNIQUE(user_id, subsystem)
        );
    ''')
    conn.executemany(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role) VALUES (?,?,?,?,?,?)",
        [
            ('u-admin', 'c1', 'ADMIN-0001', 'owner@example.com', 'hash-admin', 'admin'),
            ('u-emp-1', 'c1', 'EMP-0001', 'till1@example.com', 'hash-e1', 'employee'),
            ('u-emp-2', 'c1', 'EMP-0002', 'till2@example.com', 'hash-e2', 'employee'),
        ],
    )
    # The legacy grant shape that is live in production today.
    conn.execute(
        "INSERT INTO user_permissions (id, user_id, subsystem, access_level) VALUES (?,?,?,?)",
        ('perm-legacy', 'u-emp-1', 'retail', 'full'),
    )
    conn.execute('PRAGMA user_version = 2')
    conn.commit()
    return db_path, conn


def _migrate(tmp_path, conn, db_path):
    ensure_schema_version(
        conn, db_path, target_version=3, migrate_fn=apply_account_schema,
        backup_dir=str(tmp_path / 'backups'),
    )


@pytest.fixture
def migrated(tmp_path):
    db_path, conn = _make_v2_fixture(tmp_path)
    _migrate(tmp_path, conn, db_path)
    yield conn, db_path
    conn.close()


def _user(conn, user_id):
    return conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()


def _caps(conn, user_id):
    return {
        r['subsystem']: r['access_level']
        for r in conn.execute(
            "SELECT subsystem, access_level FROM user_permissions WHERE user_id=?", (user_id,)
        ).fetchall()
    }


# ── Columns and version ──────────────────────────────────────────────────────

def test_migration_advances_the_registry_version_to_3(tmp_path):
    db_path, conn = _make_v2_fixture(tmp_path)
    assert conn.execute('PRAGMA user_version').fetchone()[0] == 2
    _migrate(tmp_path, conn, db_path)
    assert conn.execute('PRAGMA user_version').fetchone()[0] == 3
    conn.close()


def test_users_gains_every_v3_column(migrated):
    conn, _ = migrated
    cols = {r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
    for expected in ('uid', 'pin_hash', 'row_version', 'updated_at_utc', 'deleted_at_utc'):
        assert expected in cols, f"v3 must add users.{expected}"


def test_pre_existing_user_data_survives_byte_identical(tmp_path):
    """Additive-only means additive-only: no table rebuild, so the identity
    columns of every existing row come out exactly as they went in."""
    db_path, conn = _make_v2_fixture(tmp_path)
    before = conn.execute(
        "SELECT id, company_id, employee_id, email, password_hash, session_version FROM users ORDER BY id"
    ).fetchall()

    _migrate(tmp_path, conn, db_path)

    after = conn.execute(
        "SELECT id, company_id, employee_id, email, password_hash, session_version FROM users ORDER BY id"
    ).fetchall()
    assert [tuple(r) for r in after] == [tuple(r) for r in before]
    conn.close()


# ── uid backfill ─────────────────────────────────────────────────────────────

def test_every_row_gets_a_uid_and_they_are_all_distinct_uuid4s(migrated):
    conn, _ = migrated
    uids = [r['uid'] for r in conn.execute("SELECT uid FROM users").fetchall()]
    assert len(uids) == 3
    assert all(uids), "no row may be left without a wire identity"
    assert len(set(uids)) == 3, "uids must be distinct"
    for value in uids:
        parsed = uuid.UUID(value)  # raises if it is not a real UUID
        assert parsed.version == 4, "design §8 relies on Owner's uuid.UUID() gate accepting these"


def test_uid_is_not_the_local_primary_key(migrated):
    """The local integer/UUID `id` stays a private detail of this install --
    the whole point of adding `uid` separately (design §8)."""
    conn, _ = migrated
    for row in conn.execute("SELECT id, uid FROM users").fetchall():
        assert row['uid'] != row['id']


def test_the_uid_unique_index_actually_rejects_a_duplicate(migrated):
    conn, _ = migrated
    taken = conn.execute("SELECT uid FROM users WHERE id='u-emp-1'").fetchone()['uid']
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE users SET uid=? WHERE id='u-emp-2'", (taken,))
    conn.rollback()


# ── Role widening ────────────────────────────────────────────────────────────

def test_admin_stays_admin_and_employee_becomes_cashier(migrated):
    conn, _ = migrated
    assert _user(conn, 'u-admin')['role'] == 'admin'
    assert _user(conn, 'u-emp-1')['role'] == 'cashier'
    assert _user(conn, 'u-emp-2')['role'] == 'cashier'


def test_an_empty_role_becomes_cashier_not_something_privileged(tmp_path):
    db_path, conn = _make_v2_fixture(tmp_path)
    conn.execute("INSERT INTO users (id, company_id, employee_id, email, password_hash, role) "
                 "VALUES ('u-blank', 'c1', 'EMP-0003', 'blank@example.com', 'h', NULL)")
    conn.commit()

    _migrate(tmp_path, conn, db_path)

    assert _user(conn, 'u-blank')['role'] == 'cashier'
    conn.close()


def test_a_role_the_migration_does_not_understand_is_left_alone(tmp_path):
    """Never rewrite data we cannot explain. `normalize_role` reads such a row
    as the least-privileged role at decision time, so it is powerless without
    the evidence of how it got there being destroyed."""
    db_path, conn = _make_v2_fixture(tmp_path)
    conn.execute("INSERT INTO users (id, company_id, employee_id, email, password_hash, role) "
                 "VALUES ('u-odd', 'c1', 'EMP-0004', 'odd@example.com', 'h', 'wizard')")
    conn.commit()

    _migrate(tmp_path, conn, db_path)

    assert _user(conn, 'u-odd')['role'] == 'wizard', "unknown values must survive the migration untouched"
    assert user_accounts.normalize_role('wizard') == user_accounts.ROLE_CASHIER
    conn.close()


def test_normalize_role_maps_the_legacy_value_and_defaults_least_privilege():
    assert user_accounts.normalize_role('employee') == user_accounts.ROLE_CASHIER
    assert user_accounts.normalize_role('admin') == user_accounts.ROLE_ADMIN
    assert user_accounts.normalize_role('Manager') == user_accounts.ROLE_MANAGER
    assert user_accounts.normalize_role(None) == user_accounts.ROLE_CASHIER
    assert user_accounts.normalize_role('') == user_accounts.ROLE_CASHIER
    assert user_accounts.normalize_role('superuser') == user_accounts.ROLE_CASHIER


# ── Capability seeding ───────────────────────────────────────────────────────

DESIGN_SECTION_3_CODES = (
    'retail.sell', 'retail.refund', 'retail.discount', 'retail.stock.adjust',
    'retail.reports', 'retail.cash.close', 'retail.cash.approve', 'retail.employees',
)


def test_the_eight_codes_are_exactly_the_ones_the_design_names():
    assert set(user_accounts.CAPABILITY_CODES) == set(DESIGN_SECTION_3_CODES)
    assert len(user_accounts.CAPABILITY_CODES) == 8


def test_every_user_is_seeded_with_every_capability_code(migrated):
    conn, _ = migrated
    for user_id in ('u-admin', 'u-emp-1', 'u-emp-2'):
        caps = _caps(conn, user_id)
        for code in DESIGN_SECTION_3_CODES:
            assert code in caps, f"{user_id} has no row for {code} -- 'never provisioned' and 'denied' must stay distinguishable"


def test_the_admin_is_seeded_with_every_capability(migrated):
    conn, _ = migrated
    caps = _caps(conn, 'u-admin')
    assert all(caps[code] == 'full' for code in DESIGN_SECTION_3_CODES)


def test_a_migrated_cashier_gets_least_privilege_defaults(migrated):
    """The till's own three: sell, refund against a sale already on this
    device, and close this terminal's drawer. Discounts, stock adjustments,
    reports, cash-variance approval and employee management all start denied
    -- a migration must not silently hand every existing employee the levers
    that move money or stock with no transaction behind them.

    Refund is a default and the other five are not, because `create_return`
    is sale-bound: it resolves a real `sales` row of this company or 404s,
    and recomputes every figure from the original `sale_items`. It cannot
    move money without a sale, which is the property the other five lack."""
    conn, _ = migrated
    caps = _caps(conn, 'u-emp-2')
    for granted in ('retail.sell', 'retail.refund', 'retail.cash.close'):
        assert caps[granted] == 'full', f"{granted} is part of working a till"
    for denied in ('retail.discount', 'retail.stock.adjust',
                   'retail.reports', 'retail.cash.approve', 'retail.employees'):
        assert caps[denied] == 'none', f"{denied} must not be granted to a cashier by default"


def test_a_manager_gets_neither_owner_capability():
    """A manager runs the shop floor: discounts, stock, reports, the drawer.
    Both owner codes are withheld.

    `retail.employees` because minting accounts is the owner's authority.
    `retail.cash.approve` because a role holding it ALONGSIDE
    `retail.cash.close` could count its own drawer and then sign off its own
    shortfall -- the self-approval hole AUDIT-032 closed on the Owner side,
    reintroduced through a default instead of through a route."""
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE user_permissions (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, "
                 "subsystem TEXT NOT NULL, access_level TEXT DEFAULT 'none', UNIQUE(user_id, subsystem))")
    user_accounts.seed_capabilities_for_user(conn, 'm1', 'manager')
    caps = _caps(conn, 'm1')
    assert caps['retail.employees'] == 'none'
    assert caps['retail.cash.approve'] == 'none'
    for code in set(DESIGN_SECTION_3_CODES) - {'retail.employees', 'retail.cash.approve'}:
        assert caps[code] == 'full'
    conn.close()


def test_no_role_can_both_close_a_drawer_and_approve_its_variance():
    """Stated as an invariant over every role rather than as two assertions
    about two roles, so a role added later is covered by construction. The
    owner is exempt for the reason they are always exempt: they are the
    person the separation of duties exists to protect."""
    for role in (user_accounts.ROLE_MANAGER, user_accounts.ROLE_CASHIER):
        caps = user_accounts.capabilities_for_role(role)
        assert not ({user_accounts.CAP_CASH_CLOSE, user_accounts.CAP_CASH_APPROVE} <= caps), role


def test_the_legacy_subsystem_grant_is_left_exactly_as_it_was(migrated):
    """The seeded codes are namespaced ('retail.sell'), the legacy grant is
    not ('retail'), so `mt_require_subsystem`'s existing lookup still finds
    what it always found. Seeding must change no live authorization
    decision."""
    conn, _ = migrated
    row = conn.execute(
        "SELECT id, access_level FROM user_permissions WHERE user_id='u-emp-1' AND subsystem='retail'"
    ).fetchone()
    assert row is not None
    assert row['id'] == 'perm-legacy', "the pre-existing row must survive, not be replaced"
    assert row['access_level'] == 'full'


def test_seeding_never_overwrites_an_admin_edited_capability(migrated):
    """Re-running the seed after an owner has tuned a capability must leave
    their edit alone -- this is what makes the migration safe to retry after
    a partial failure."""
    conn, _ = migrated
    # retail.stock.adjust specifically: it is 'none' in the cashier default,
    # so 'full' here can ONLY have come from the admin's edit surviving. A
    # code the default already grants would assert nothing -- it would read
    # 'full' whether the seed respected the edit or trampled it.
    assert _caps(conn, 'u-emp-2')['retail.stock.adjust'] == 'none'
    conn.execute(
        "UPDATE user_permissions SET access_level='full' "
        "WHERE user_id='u-emp-2' AND subsystem='retail.stock.adjust'"
    )
    conn.commit()

    user_accounts.seed_capabilities_for_user(conn, 'u-emp-2', 'cashier')
    conn.commit()

    assert _caps(conn, 'u-emp-2')['retail.stock.adjust'] == 'full'


def test_user_has_capability_reads_the_seeded_rows_and_fails_closed(migrated):
    conn, _ = migrated
    assert user_accounts.user_has_capability(conn, 'u-emp-2', 'retail.sell') is True
    assert user_accounts.user_has_capability(conn, 'u-emp-2', 'retail.stock.adjust') is False
    assert user_accounts.user_has_capability(conn, 'u-emp-2', 'retail.never.seeded') is False
    assert user_accounts.user_has_capability(conn, 'no-such-user', 'retail.sell') is False


# ── Row metadata ─────────────────────────────────────────────────────────────

def test_row_version_and_updated_at_are_populated_and_deleted_at_is_not(migrated):
    conn, _ = migrated
    for row in conn.execute("SELECT row_version, updated_at_utc, deleted_at_utc FROM users").fetchall():
        assert row['row_version'] == 1
        assert row['updated_at_utc'], "the sync layer's last-write marker must not be NULL"
        assert row['deleted_at_utc'] is None, "a migration must not tombstone anybody"


# ── Idempotency ──────────────────────────────────────────────────────────────

def test_running_the_migration_twice_changes_nothing(migrated):
    conn, _ = migrated
    before_users = [tuple(r) for r in conn.execute("SELECT * FROM users ORDER BY id").fetchall()]
    before_perm_count = conn.execute("SELECT COUNT(*) FROM user_permissions").fetchone()[0]

    apply_account_schema(conn)  # straight through, bypassing the version gate

    after_users = [tuple(r) for r in conn.execute("SELECT * FROM users ORDER BY id").fetchall()]
    assert after_users == before_users, "a re-run must not reissue uids or re-map roles"
    assert conn.execute("SELECT COUNT(*) FROM user_permissions").fetchone()[0] == before_perm_count


def test_a_fresh_v0_database_reaches_v3_in_one_pass(tmp_path):
    """The real upgrade shape for an install that predates PRAGMA
    user_version on registry.db entirely."""
    db_path, conn = _make_v2_fixture(tmp_path)
    conn.execute('PRAGMA user_version = 0')
    conn.commit()

    _migrate(tmp_path, conn, db_path)

    assert conn.execute('PRAGMA user_version').fetchone()[0] == 3
    assert _user(conn, 'u-emp-1')['uid']
    conn.close()


# ── PIN storage: attribution, never authorization ────────────────────────────

@pytest.fixture
def pin_conn(migrated):
    from commercial_runtime.identity.registry_sync_schema import apply_registry_sync_schema

    conn, _ = migrated
    # `set_user_pin`/`clear_user_pin` queue a `user` sync event as of wave B2
    # stage 2b, and that write lands in registry.db's OWN `sync_outbox` -- a
    # table registry v5 adds, well after the v3 migration the fixtures above
    # deliberately stop at. Without this the five PIN tests below fail with
    # `no such table: sync_outbox`.
    #
    # The v3 floor is correct for the migration assertions above and wrong
    # here, and the difference is worth being precise about: those tests
    # exercise the MIGRATION, so they must stay pinned at exactly 3. These
    # five call PRODUCTION write functions, and in production
    # `init_registry_db()` has always brought the registry to the CURRENT
    # version before any code path can reach `set_user_pin`. A fixture frozen
    # at v3 is testing a state no running install is ever in.
    #
    # Only the sync schema is applied, rather than bumping this file's whole
    # fixture to current, so the `user_version == 3` assertions above keep
    # meaning exactly what they mean today.
    #
    # What this can no longer catch: `set_user_pin` acquiring a dependency on
    # some FUTURE registry table would show up here as a green test rather
    # than a missing-table failure. That is the same trade every other test in
    # this repo that runs against a current-schema database already makes, and
    # the emission itself is separately pinned by
    # `commercial_runtime/identity/tests/test_user_sync_emission_write_sites.py`.
    apply_registry_sync_schema(conn)
    return conn


def test_a_pin_is_stored_hashed_never_in_plaintext_and_never_as_a_bare_digest(pin_conn):
    import hashlib

    user_accounts.set_user_pin(pin_conn, 'u-emp-1', '4821')
    pin_conn.commit()

    stored = _user(pin_conn, 'u-emp-1')['pin_hash']
    assert stored
    assert '4821' not in stored, "the PIN itself must never appear in the column"
    assert stored != hashlib.sha256(b'4821').hexdigest(), "a bare digest of four digits is a lookup table away from plaintext"
    assert stored.startswith('pbkdf2_sha256$'), "PINs use the same KDF the password column uses"
    assert verify_password('4821', stored) is True


def test_verify_user_pin_accepts_the_right_pin_and_refuses_everything_else(pin_conn):
    user_accounts.set_user_pin(pin_conn, 'u-emp-1', '1357')
    pin_conn.commit()

    assert user_accounts.verify_user_pin(pin_conn, 'u-emp-1', '1357') is True
    assert user_accounts.verify_user_pin(pin_conn, 'u-emp-1', '1358') is False
    assert user_accounts.verify_user_pin(pin_conn, 'u-emp-1', '') is False
    assert user_accounts.verify_user_pin(pin_conn, 'u-emp-1', None) is False
    assert user_accounts.verify_user_pin(pin_conn, 'u-emp-2', '1357') is False, \
        "one user's PIN must not identify another user"


def test_a_user_with_no_pin_can_never_be_matched(pin_conn):
    assert _user(pin_conn, 'u-emp-2')['pin_hash'] is None
    for attempt in ('0000', '1234', '9999', ''):
        assert user_accounts.verify_user_pin(pin_conn, 'u-emp-2', attempt) is False


def test_setting_a_pin_that_is_not_four_digits_is_refused(pin_conn):
    for bad in ('123', '12345', 'abcd', '12 4', '', None, '12-4', '²²²²'):
        with pytest.raises(user_accounts.PinPolicyError):
            user_accounts.set_user_pin(pin_conn, 'u-emp-1', bad)


def test_a_pin_typed_with_arabic_indic_digits_is_the_same_pin(pin_conn):
    """This product ships Arabic and is RTL, and an Arabic soft keyboard emits
    ARABIC-INDIC digits (U+0660..U+0669). '١٢٣٤' and '1234'
    must be ONE pin -- otherwise a cashier who sets their PIN on the phone and
    types it on the desktop keypad is locked out by a keyboard layout.

    Python's `\\d` matches Arabic-Indic digits, so a naive `^\\d{4}$` check
    ACCEPTS this input and stores it verbatim: the account looks fine and can
    never be verified again. The fold has to happen on both sides."""
    arabic_indic = '١٢٣٤'
    user_accounts.set_user_pin(pin_conn, 'u-emp-1', arabic_indic)
    pin_conn.commit()

    assert user_accounts.verify_user_pin(pin_conn, 'u-emp-1', '1234') is True
    assert user_accounts.verify_user_pin(pin_conn, 'u-emp-1', arabic_indic) is True
    assert user_accounts.verify_user_pin(pin_conn, 'u-emp-1', '4321') is False

    stored = _user(pin_conn, 'u-emp-1')['pin_hash']
    assert arabic_indic not in stored


def test_setting_a_pin_moves_the_row_version_and_the_update_stamp(pin_conn):
    before = _user(pin_conn, 'u-emp-1')
    user_accounts.set_user_pin(pin_conn, 'u-emp-1', '2468')
    pin_conn.commit()
    after = _user(pin_conn, 'u-emp-1')

    assert after['row_version'] == before['row_version'] + 1, \
        "a shared, single-writer row that changed must not look unchanged to a peer"
    assert after['updated_at_utc'] != before['updated_at_utc']


def test_clearing_a_pin_removes_it_and_still_moves_the_row_version(pin_conn):
    user_accounts.set_user_pin(pin_conn, 'u-emp-1', '1111')
    pin_conn.commit()
    mid = _user(pin_conn, 'u-emp-1')

    user_accounts.clear_user_pin(pin_conn, 'u-emp-1')
    pin_conn.commit()
    after = _user(pin_conn, 'u-emp-1')

    assert after['pin_hash'] is None
    assert after['row_version'] == mid['row_version'] + 1
    assert user_accounts.verify_user_pin(pin_conn, 'u-emp-1', '1111') is False


def test_a_pin_never_authorizes_the_four_actions_the_design_reserves_for_a_password():
    """Design §3: "A PIN alone never authorises: void of a closed sale,
    cost/price edit, employee management, or cash-variance approval -- those
    re-prompt for the password.\""""
    for action in ('retail.sale.void_closed', 'retail.product.cost_or_price.edit',
                   'retail.employees', 'retail.cash.approve'):
        assert user_accounts.requires_password_reprompt(action) is True, \
            f"{action} must re-prompt for the password, never accept a PIN"


def test_selling_does_not_require_a_password_reprompt():
    """The guard against 'implemented' by demanding a password for
    everything, which would make the PIN switch pointless."""
    for action in ('retail.sell', 'retail.refund', 'retail.discount',
                   'retail.stock.adjust', 'retail.reports', 'retail.cash.close'):
        assert user_accounts.requires_password_reprompt(action) is False


def test_there_is_no_way_to_turn_a_pin_into_a_permission():
    """A PIN is attribution. The module deliberately exposes no function that
    answers "what may this PIN do", because such a function is the whole
    mistake the rule exists to prevent -- if one is ever added, this test is
    where the argument for it has to be made."""
    exported = {name for name in dir(user_accounts) if not name.startswith('_')}
    forbidden = {'pin_grants', 'pin_can_authorize', 'authorize_pin',
                 'pin_capabilities', 'login_with_pin', 'resolve_pin_to_user'}
    assert exported & forbidden == set()


# ── The real wiring: a live registry.db reaches v3 ───────────────────────────

def test_the_live_registry_database_is_at_version_3():
    """This file's whole subject is registry v3 (see module docstring), so
    the floor asserted here is 3, not an exact match against whatever
    REGISTRY_SCHEMA_VERSION happens to be today -- a later wave's own
    migration (v4, launch-readiness Phase 5 prerequisite #1 -- see
    commercial_runtime/identity/company_rebind.py) legitimately advances the
    live constant further, and pinning an exact number here would make this
    test fail on every future version bump for a reason that has nothing to
    do with what it actually verifies: that a live registry.db really does
    converge to whatever version the running code expects. Same "assert a
    floor, not an exact count, so a later additive migration doesn't fail
    this test for the wrong reason" convention this codebase already uses
    everywhere else a schema version could be asserted (e.g. products/retail/
    tests/retail_v14_company_rebind_migration_test.py's table-count floor) --
    this file was the one place that convention had been missed."""
    registry_db.init_registry_db()
    conn = registry_db.get_conn()
    try:
        assert registry_db.REGISTRY_SCHEMA_VERSION >= 3
        assert conn.execute('PRAGMA user_version').fetchone()[0] == registry_db.REGISTRY_SCHEMA_VERSION
    finally:
        conn.close()


def _admin_client():
    """An admin session. create-admin is gated on "no valid admin exists", so
    the first test to call it wins; every later caller logs in instead."""
    client = app.test_client()
    r = client.post('/api/onboarding/create-admin', json={
        'name': 'Owner', 'email': 'v3-owner@test.local', 'password': 'OwnerPW11',
    })
    if r.status_code == 409:
        r = client.post('/api/auth/login', json={'email': 'v3-owner@test.local', 'password': 'OwnerPW11'})
    assert r.status_code == 200, r.get_json()
    return client


def _created_employee_id(email):
    conn = registry_db.get_conn()
    try:
        return conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()['id']
    finally:
        conn.close()


def test_a_newly_created_owner_gets_a_uid_and_a_full_capability_grid():
    """The v3 backfill runs once, ever. If the account-creation routes did
    not set `uid` themselves, every account created after upgrade day would
    have no wire identity and would be invisible to a second device -- the
    migration would look correct and the feature would still be broken."""
    _admin_client()

    conn = registry_db.get_conn()
    try:
        row = conn.execute(
            "SELECT id, uid, role, row_version, updated_at_utc FROM users WHERE email=?",
            ('v3-owner@test.local',),
        ).fetchone()
        assert row['uid'], "a newly created owner must have a wire identity"
        assert uuid.UUID(row['uid']).version == 4
        assert row['uid'] != row['id']
        assert row['role'] == 'admin'
        assert row['row_version'] == 1
        assert row['updated_at_utc'] and row['updated_at_utc'].endswith('+00:00'), \
            "updated_at_utc must be timezone-aware UTC so it compares correctly against the backfilled rows"

        caps = _caps(conn, row['id'])
        assert set(caps) >= set(DESIGN_SECTION_3_CODES)
        assert all(caps[code] == 'full' for code in DESIGN_SECTION_3_CODES)
    finally:
        conn.close()


def test_create_employee_defaults_to_the_legacy_role_and_seeds_cashier_capabilities():
    """The default stays 'employee' for Clinic's sake (see create_employee's
    comment), and `normalize_role` makes that a cashier everywhere it
    matters -- including in what gets seeded."""
    admin = _admin_client()
    email = f'v3-emp-default-{uuid.uuid4().hex[:6]}@test.local'
    r = admin.post('/api/admin/employees', json={'email': email})
    assert r.status_code == 200, r.get_json()

    conn = registry_db.get_conn()
    try:
        row = conn.execute("SELECT id, uid, role FROM users WHERE email=?", (email,)).fetchone()
        assert row['role'] == 'employee'
        assert row['uid'], "an employee created after the migration still needs a wire identity"
        caps = _caps(conn, row['id'])
        # The till's three, and nothing above them. Asserted as the FULL set
        # rather than a sample, so a code that quietly joins the cashier
        # default later cannot slip past this test.
        granted = {code for code, level in caps.items()
                   if code.startswith('retail.') and code != 'retail' and level == 'full'}
        assert granted == {'retail.sell', 'retail.refund', 'retail.cash.close'}, granted
        assert caps['retail.employees'] == 'none'
        assert caps['retail.stock.adjust'] == 'none'
    finally:
        conn.close()


def test_create_employee_can_mint_a_manager_with_the_manager_capability_set():
    admin = _admin_client()
    email = f'v3-emp-mgr-{uuid.uuid4().hex[:6]}@test.local'
    r = admin.post('/api/admin/employees', json={'email': email, 'role': 'manager'})
    assert r.status_code == 200, r.get_json()

    conn = registry_db.get_conn()
    try:
        row = conn.execute("SELECT id, role FROM users WHERE email=?", (email,)).fetchone()
        assert row['role'] == 'manager'
        caps = _caps(conn, row['id'])
        assert caps['retail.refund'] == 'full'
        assert caps['retail.discount'] == 'full'
        assert caps['retail.stock.adjust'] == 'full'
        assert caps['retail.reports'] == 'full'
        assert caps['retail.employees'] == 'none', "only the owner manages accounts"
        assert caps['retail.cash.approve'] == 'none', \
            "a manager closes drawers, so granting variance approval would let them sign off their own"
    finally:
        conn.close()


def test_create_employee_refuses_a_role_outside_the_widened_domain():
    """Refuse, do not quietly downgrade: an admin who meant to create a
    manager must not silently end up with a cashier. 'admin' is refused too
    -- this install allows exactly one owner account, so there is no
    'promote to admin' operation to expose here."""
    admin = _admin_client()
    r = admin.post('/api/admin/employees', json={
        'email': f'v3-emp-bad-{uuid.uuid4().hex[:6]}@test.local', 'role': 'admin',
    })
    assert r.status_code == 400
    r2 = admin.post('/api/admin/employees', json={
        'email': f'v3-emp-bad2-{uuid.uuid4().hex[:6]}@test.local', 'role': 'wizard',
    })
    assert r2.status_code == 400


def test_the_new_user_visible_strings_exist_in_both_locale_catalogs():
    """This product ships Arabic and is RTL. i18n.js translates by matching
    the FULL English sentence against the catalogs, so a message assembled at
    runtime -- or one that was never added as a key -- renders in English on
    an Arabic till. Asserting the string the ROUTE really returns, not a copy
    of it, is what stops the code and the catalog drifting apart."""
    locales = PRODUCT_DIR / 'frontend' / 'locales'
    en = json.loads((locales / 'en.json').read_text(encoding='utf-8'))
    ar = json.loads((locales / 'ar.json').read_text(encoding='utf-8'))

    admin = _admin_client()
    r = admin.post('/api/admin/employees', json={
        'email': f'v3-emp-i18n-{uuid.uuid4().hex[:6]}@test.local', 'role': 'wizard',
    })
    assert r.status_code == 400
    role_message = r.get_json()['error']

    conn = sqlite3.connect(':memory:')
    conn.execute("CREATE TABLE users (id TEXT PRIMARY KEY, pin_hash TEXT, row_version INTEGER, updated_at_utc TEXT)")
    conn.execute("INSERT INTO users (id, row_version) VALUES ('x', 1)")
    with pytest.raises(user_accounts.PinPolicyError) as excinfo:
        user_accounts.set_user_pin(conn, 'x', '12')
    pin_message = str(excinfo.value)
    conn.close()

    for text in (role_message, pin_message):
        assert text in en, f"missing from en.json: {text!r}"
        assert text in ar, f"missing from ar.json: {text!r}"
        assert ar[text].strip() and ar[text] != en[text], f"not actually translated: {text!r}"


def test_an_explicit_permission_beats_the_seeded_default():
    """The seed fills gaps; it never overrules what the admin asked for on
    the same request."""
    admin = _admin_client()
    email = f'v3-emp-perm-{uuid.uuid4().hex[:6]}@test.local'
    r = admin.post('/api/admin/employees', json={
        'email': email, 'role': 'cashier', 'permissions': {'retail.refund': 'full', 'retail': 'full'},
    })
    assert r.status_code == 200, r.get_json()

    conn = registry_db.get_conn()
    try:
        caps = _caps(conn, _created_employee_id(email))
        assert caps['retail.refund'] == 'full', "the admin's explicit grant must survive the seed"
        assert caps['retail'] == 'full', "the legacy subsystem grant still works alongside the codes"
        assert caps['retail.discount'] == 'none', "everything the admin did not name keeps the role default"
    finally:
        conn.close()


def _row_version(user_id):
    conn = registry_db.get_conn()
    try:
        return conn.execute("SELECT row_version FROM users WHERE id=?", (user_id,)).fetchone()['row_version']
    finally:
        conn.close()


def test_every_account_management_write_moves_the_row_version():
    """`users` is a shared, admin-device single-writer table (design §4) and
    `row_version` is the reject-stale marker a peer compares. A row that
    changed without moving its version is a row the other device will decline
    to take -- so status changes, role/permission changes and password resets
    all have to move it, not just the PIN writes that go through
    `_touch_user`."""
    admin = _admin_client()
    email = f'v3-emp-ver-{uuid.uuid4().hex[:6]}@test.local'
    assert admin.post('/api/admin/employees', json={'email': email, 'role': 'cashier'}).status_code == 200
    user_id = _created_employee_id(email)

    after_create = _row_version(user_id)
    assert after_create == 1

    assert admin.post(f'/api/admin/employees/{user_id}/permissions',
                      json={'subsystem': 'retail.refund', 'access_level': 'full'}).status_code == 200
    after_perms = _row_version(user_id)
    assert after_perms > after_create, "a permission change must move the row version"

    assert admin.put(f'/api/admin/employees/{user_id}/status',
                     json={'status': 'disabled'}).status_code == 200
    assert _row_version(user_id) > after_perms, "a status change must move the row version"


def test_every_account_on_the_install_has_a_distinct_uid():
    _admin_client()
    conn = registry_db.get_conn()
    try:
        uids = [r['uid'] for r in conn.execute("SELECT uid FROM users").fetchall()]
        assert all(uids), "no account may be left without a wire identity"
        assert len(set(uids)) == len(uids)
    finally:
        conn.close()


def test_a_live_registry_user_is_seeded_and_given_a_uid():
    """End to end through registry_db, not the synthetic fixture: a row that
    exists before the migration runs comes out with a wire identity and a
    full capability grid."""
    registry_db.init_registry_db()
    conn = registry_db.get_conn()
    try:
        user_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, "
            "require_password_change, uid, updated_at_utc) VALUES (?,?,?,?,?,?,?,0,?,?)",
            (user_id, 'live-co', f'EMP-{uuid.uuid4().hex[:8]}', f'live-{uuid.uuid4().hex[:8]}@test.local',
             hash_password('LivePW1'), 'cashier', 'active', str(uuid.uuid4()), '2026-08-20T00:00:00+00:00'),
        )
        user_accounts.seed_capabilities_for_user(conn, user_id, 'cashier')
        conn.commit()

        caps = _caps(conn, user_id)
        assert set(caps) >= set(DESIGN_SECTION_3_CODES)
        assert caps['retail.sell'] == 'full'
        assert caps['retail.employees'] == 'none'
    finally:
        conn.close()
