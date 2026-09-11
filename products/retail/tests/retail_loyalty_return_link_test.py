"""
Aura Retail -- the loyalty return link (schema v29, ROADMAP.md's
"CLAIMED: retail v29" entry).

v27 added `loyalty_ledger`. When `create_return` reverses loyalty points it
writes two immutable rows (an 'adjust' earn-clawback and an 'adjust'
redeem-giveback), and both went in with `sale_id` NULL, because the table
had no `return_id` column -- the ledger alone could not answer "which
return reversed these points", only the audit log's free-text detail
could. v29 adds the column:

    ALTER TABLE loyalty_ledger ADD COLUMN return_id INTEGER

WHAT THIS FILE PROVES, in order (numbered to match ROADMAP.md's own report
shape and this task's plan):

  1. MIGRATION ON A REAL v28 DATABASE (not a fresh v29 install, which would
     already carry the column and prove nothing about the ALTER path
     itself): `_migrate_add_loyalty_return_id` adds a NULLABLE INTEGER
     `return_id` column and the `idx_loyalty_ledger_return` partial index.
  2. IDEMPOTENCE: running the step twice changes nothing in `sqlite_master`
     and leaves `PRAGMA integrity_check` at 'ok'.
  3. THE POSITIVE: ringing a sale that earns and redeems points, then
     returning part of it, writes reversal rows whose `return_id` equals
     the id of the return that was JUST created -- the actual value, not
     merely "is not None".
  4. THE NEGATIVE (the half that is easy to skip): the sale's own 'earn'
     row keeps `return_id` NULL. A test that only checks the reversal rows
     cannot tell "the column is set correctly" from "every row gets a
     return id" -- this is the case that catches the second failure shape.
  5. A SECOND, independent return against the SAME sale writes rows
     carrying ITS OWN id, not the first return's -- proving the value
     tracks the individual return, not the customer or the sale.
  6. NO BACKFILL: a pre-existing 'adjust' row written before this column
     existed stays NULL forever after the migration runs, exactly as
     designed (see _migrate_add_loyalty_return_id's own docstring in
     database/schema.py for why a backfill here would just be a guess
     wearing a foreign key's clothes).

MUTATION-PROVED (see this session's own report for both directions of each
run, not encoded here as source-mutating test code -- same convention
retail_return_loyalty_reversal_test.py's own module docstring already
states for this file's sibling): case 3 was confirmed to go RED when the
INSERT statements were reverted to omit `return_id` entirely, and case 4
was confirmed to go RED when the earn row's own INSERT (create_sale) was
changed to also stamp a `return_id`. Both back to GREEN on revert.

Self-contained bootstrap, matching retail_return_loyalty_reversal_test.py
and the rest of this suite (no shared conftest.py exists here). CRITICAL:
exactly ONE pytest process per file -- AURA_APP_DATA resolves at import
time, so two test files sharing one pytest invocation corrupt each other
(AUDIT-010).

Run:
    py -3.14 -m pytest products/retail/tests/retail_loyalty_return_link_test.py -v
"""
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_loyalty_return_link_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402

seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")
os.environ.pop("AURA_RETAIL_DEMO_MODE", None)

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

import database.schema as sch  # noqa: E402
from commercial_runtime.identity import user_accounts  # noqa: E402
from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402

API = '/api/sub/retail'


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


# ── Fixtures / helpers (deliberately duplicated per-file convention -- see
#    retail_product_lookup_nocase_test.py's own module docstring for why
#    nothing here is shared via a conftest.py). Copied verbatim in shape
#    from retail_return_loyalty_reversal_test.py's own helpers, trimmed to
#    only what this file's cases actually need. ─────────────────────────────

def _make_user(role, company_id, capabilities=None):
    """A real account with real capability rows -- matches
    retail_loyalty_redemption_test.py's own `_make_user`."""
    email = f"retloylink-{role}-{uuid.uuid4().hex[:10]}@test.local"
    # Same exemption, and same reasoning, as retail_loyalty_redemption_test.py:
    # a fixture password for a throwaway user this test creates. detect-secrets
    # flags any `password = "..."`; the pragma keeps the exemption visible in
    # the file rather than buried in a 221-entry baseline.
    password = "RetLoyLinkPW1"  # pragma: allowlist secret
    user_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, "
        "require_password_change) VALUES (?,?,?,?,?,?,?,0)",
        (user_id, company_id, f"EMP-{uuid.uuid4().hex[:6]}", email, hash_password(password), role, "active"),
    )
    conn.execute(
        "INSERT INTO user_permissions (id, user_id, subsystem, access_level) VALUES (?,?,?,?)",
        (str(uuid.uuid4()), user_id, "retail", "full"),
    )
    user_accounts.seed_capabilities_for_user(conn, user_id, role)
    if capabilities:
        for code, level in capabilities.items():
            conn.execute(
                "UPDATE user_permissions SET access_level=? WHERE user_id=? AND subsystem=?",
                (level, user_id, code),
            )
    conn.commit()
    conn.close()

    client = app.test_client()
    r = client.post('/api/auth/login', json={'email': email, 'password': password})
    assert r.status_code == 200, r.get_json()
    return client


def _new_shop():
    """A fresh company with one logged-in ADMIN user (bypasses every
    capability gate) -- each test gets its OWN company so ledger balances
    and settings from one test never leak into another's assertions."""
    company_id = str(uuid.uuid4())
    admin = _make_user('admin', company_id)
    admin.get(f'{API}/settings/tax')  # forces _ensure_credit_schema/doc_sequences, matching sibling files
    return company_id, admin


@pytest.fixture
def shop():
    return _new_shop()


def _create_product(client, sell_price=100.0, tax_rate=0):
    tag = uuid.uuid4().hex[:8]
    payload = {
        'name': f'Return-link item {tag}', 'sku': f'RETLOYLINK-{tag}',
        'sell_price': sell_price, 'cost_price': 1.0, 'tax_rate': tax_rate,
        'initial_stock': 1000,
    }
    r = client.post(f'{API}/products', json=payload)
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _create_customer(client):
    payload = {'name': f'Return Link Customer {uuid.uuid4().hex[:6]}'}
    r = client.post(f'{API}/customers', json=payload)
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _sale(client, items, **extra):
    payload = dict({'items': items, 'payment_method': 'cash',
                     'idempotency_key': str(uuid.uuid4())}, **extra)
    return client.post(f'{API}/sales', json=payload)


def _return(client, sale_id, items, reason='test return'):
    payload = {'sale_id': sale_id, 'items': items, 'reason': reason,
               'idempotency_key': str(uuid.uuid4())}
    return client.post(f'{API}/returns', json=payload)


def _set_loyalty_point_value(client, value):
    r = client.post(f'{API}/settings/credit', json={'loyalty_point_value': value})
    assert r.status_code == 200, r.get_json()


def _seed_loyalty_balance(company_id, customer_id, points):
    """Seeds a STARTING balance the way the real `_migrate_add_loyalty_ledger`
    backfill does: an 'opening' ledger row AND the matching
    `customers.loyalty_points` cache value, kept IN SYNC from the start --
    see retail_return_loyalty_reversal_test.py's own identical helper for
    the full reasoning."""
    conn = sch.get_retail_conn()
    conn.execute(
        "INSERT INTO loyalty_ledger (uid,company_id,customer_id,points_delta,entry_type,created_by) "
        "VALUES (?,?,?,?,'opening',?)",
        (str(uuid.uuid4()), company_id, customer_id, points, 'test-fixture'))
    conn.execute("UPDATE customers SET loyalty_points=? WHERE id=?", (points, customer_id))
    conn.commit()
    conn.close()


def _ledger_rows(company_id, customer_id):
    conn = sch.get_retail_conn()
    rows = conn.execute(
        "SELECT * FROM loyalty_ledger WHERE company_id=? AND customer_id=? ORDER BY id",
        (company_id, customer_id)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _adjust_rows(company_id, customer_id):
    """Only the rows a return's reversal could have written -- see
    retail_return_loyalty_reversal_test.py's own identical helper."""
    return [r for r in _ledger_rows(company_id, customer_id) if r['entry_type'] == 'adjust']


# ── Migration-level fixture (raw sqlite3, no Flask app involved) ───────────

def _build_v28_database(path):
    """A REAL v28-shaped `loyalty_ledger`, built by the ACTUAL v27 migration
    function (`_migrate_add_loyalty_ledger`) rather than a hand-typed
    CREATE TABLE that could drift from schema.py's own definition. v28's
    own migration (`_migrate_add_stock_transfers`) never reads or writes
    `loyalty_ledger` at all -- it only adds the two, wholly separate
    `stock_transfers`/`stock_transfer_items` tables -- so the table this
    produces IS the real v28 shape, not an approximation of it.

    Deliberately does NOT run the whole `_migrate_retail_schema` chain,
    which requires the base tables `_init_retail`'s own CREATE TABLE script
    creates first (branches, categories, products, ...), none of which
    `_migrate_add_loyalty_return_id` ever reads or writes. `_migrate_add_
    loyalty_ledger` itself already guards every OTHER table it optionally
    touches ('customers' for its backfill, 'sales' for its own column) with
    the identical `'x' in live_tables` check `_migrate_add_loyalty_return_id`
    uses for `loyalty_ledger` -- so calling it against a bare file with
    neither table present is exactly the shape it is already written to
    tolerate, not a shortcut around it.

    `PRAGMA user_version` is stamped 28 by hand at the end -- nothing here
    goes through `ensure_schema_version`, which needs a single registered
    app-wide db path this fixture deliberately does not share (it would
    otherwise run its integrity checks and backups against the wrong
    file).
    """
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    sch._migrate_add_loyalty_ledger(conn)
    conn.commit()
    conn.execute("PRAGMA user_version = 28")
    conn.commit()
    return conn


# ── 1. Migration adds return_id on a REAL v28 database ─────────────────────

def test_migration_adds_return_id_column_on_a_real_v28_database(tmp_path):
    conn = _build_v28_database(tmp_path / "v28_retail.db")
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 28
        cols_before = [r['name'] for r in conn.execute("PRAGMA table_info(loyalty_ledger)").fetchall()]
        assert 'return_id' not in cols_before, "setup sanity: the v28 shape must not already carry the column"
        idx_before = {r['name'] for r in conn.execute("PRAGMA index_list(loyalty_ledger)").fetchall()}
        assert 'idx_loyalty_ledger_return' not in idx_before

        sch._migrate_add_loyalty_return_id(conn)
        conn.commit()

        col_rows = {r['name']: r for r in conn.execute("PRAGMA table_info(loyalty_ledger)").fetchall()}
        assert 'return_id' in col_rows, "the migration must add the column"
        return_id_col = col_rows['return_id']
        assert return_id_col['type'].upper() == 'INTEGER', \
            "return_id must be INTEGER -- returns.id is INTEGER PRIMARY KEY AUTOINCREMENT"
        assert return_id_col['notnull'] == 0, \
            "return_id must be NULLABLE -- pre-v29 rows and non-return adjustments keep NULL"

        idx_after = {r['name'] for r in conn.execute("PRAGMA index_list(loyalty_ledger)").fetchall()}
        assert 'idx_loyalty_ledger_return' in idx_after, "the partial index must be created"
    finally:
        conn.close()


# ── 2. Idempotence ──────────────────────────────────────────────────────────

def test_migration_is_idempotent(tmp_path):
    conn = _build_v28_database(tmp_path / "v28_retail.db")
    try:
        sch._migrate_add_loyalty_return_id(conn)
        conn.commit()
        objects_before = [dict(r) for r in conn.execute(
            "SELECT type, name FROM sqlite_master ORDER BY type, name")]

        sch._migrate_add_loyalty_return_id(conn)  # second, unnecessary pass
        conn.commit()
        objects_after = [dict(r) for r in conn.execute(
            "SELECT type, name FROM sqlite_master ORDER BY type, name")]

        assert len(objects_before) == len(objects_after), \
            "a second call must not create a duplicate column, table, or index"
        assert objects_before == objects_after, \
            "sqlite_master's own object list must be byte-identical after a redundant second call"
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == 'ok'
    finally:
        conn.close()


# ── 6. No backfill: a pre-existing adjust row stays NULL forever ───────────

def test_pre_existing_adjust_row_without_the_column_stays_null_no_backfill(tmp_path):
    """The exact v27 shape this migration exists to close the gap FOR: a
    reversal 'adjust' row written before `return_id` existed. No backfill
    (see _migrate_add_loyalty_return_id's own docstring, "NO BACKFILL") --
    this row's tie to whatever return caused it still lives only in the
    audit log, never reconstructed by the migration itself."""
    conn = _build_v28_database(tmp_path / "v28_retail.db")
    try:
        conn.execute(
            "INSERT INTO loyalty_ledger (uid, company_id, customer_id, points_delta, entry_type, created_by) "
            "VALUES (?,?,?,?,'adjust',?)",
            (str(uuid.uuid4()), 1, 'legacy-customer', -5.0, 'test-fixture'))
        conn.commit()
        legacy_id = conn.execute(
            "SELECT id FROM loyalty_ledger WHERE entry_type='adjust'"
        ).fetchone()['id']

        sch._migrate_add_loyalty_return_id(conn)
        conn.commit()

        row = conn.execute(
            "SELECT return_id FROM loyalty_ledger WHERE id=?", (legacy_id,)
        ).fetchone()
        assert row['return_id'] is None, \
            "no backfill -- a pre-v29 adjust row must keep return_id NULL forever"
    finally:
        conn.close()


# ── 3. THE POSITIVE: reversal rows carry the return_id that caused them ────

def test_return_reversal_rows_carry_the_return_id_that_caused_them(shop):
    """Rings a sale that both earns and redeems points, returns it, and
    asserts BOTH reversal rows carry `return_id` equal to the id of the
    return that was just created -- the actual value, per this task's own
    instruction, not merely "is not None"."""
    cid, admin = shop
    pid = _create_product(admin, sell_price=100.0)  # total=100 -> earns int(100/10)=10
    customer_id = _create_customer(admin)
    _set_loyalty_point_value(admin, 1)
    _seed_loyalty_balance(cid, customer_id, 50)

    r = _sale(admin, [{'product_id': pid, 'quantity': 1}],
              customer_id=customer_id, points_redeemed=20, amount_paid=999999)
    assert r.status_code == 200, r.get_json()
    sale = r.get_json()['data']

    rr = _return(admin, sale['id'], [{'product_id': pid, 'quantity': 1}])
    assert rr.status_code == 200, rr.get_json()
    return_id = rr.get_json()['data']['id']
    assert isinstance(return_id, int)

    adjusts = _adjust_rows(cid, customer_id)
    # Full return of a sale that both earned (10) and redeemed (20) points:
    # one earn-clawback row and one redeem-giveback row, exactly like
    # retail_return_loyalty_reversal_test.py's own case 1/case 2 fixtures.
    assert len(adjusts) == 2, adjusts
    for row in adjusts:
        assert row['return_id'] == return_id, (
            "every reversal row THIS return writes must carry ITS OWN return_id", row, return_id)


# ── 4. THE NEGATIVE: the original earn row keeps return_id NULL ───────────

def test_earn_row_itself_keeps_return_id_null(shop):
    """The half that is easy to skip and must not be: a test that only
    checks the reversal rows cannot tell "the column is set correctly" from
    "every row gets stamped with a return id". Rings a sale (writing one
    'earn' row), returns it, and asserts the ORIGINAL 'earn' row's
    return_id is STILL NULL -- only the NEW 'adjust' rows the return caused
    may ever carry one."""
    cid, admin = shop
    pid = _create_product(admin, sell_price=100.0)  # earns 10
    customer_id = _create_customer(admin)

    r = _sale(admin, [{'product_id': pid, 'quantity': 1}], customer_id=customer_id, amount_paid=999999)
    assert r.status_code == 200, r.get_json()
    sale = r.get_json()['data']

    earn_rows = [row for row in _ledger_rows(cid, customer_id) if row['entry_type'] == 'earn']
    assert len(earn_rows) == 1, earn_rows
    earn_id = earn_rows[0]['id']
    assert earn_rows[0]['return_id'] is None, "setup sanity: a fresh earn row must start with return_id NULL"

    rr = _return(admin, sale['id'], [{'product_id': pid, 'quantity': 1}])
    assert rr.status_code == 200, rr.get_json()

    earn_row_after = [row for row in _ledger_rows(cid, customer_id) if row['id'] == earn_id][0]
    assert earn_row_after['return_id'] is None, (
        "the original earn row must never be stamped with a return_id -- only its own reversal rows may")


# ── 5. A second, independent return writes ITS OWN return_id ──────────────

def test_second_independent_return_writes_its_own_return_id(shop):
    """Two SEPARATE returns against the SAME sale must each write reversal
    rows carrying THEIR OWN return_id -- proving the value tracks the
    individual return, not the customer or the sale it reverses against."""
    cid, admin = shop
    pid_a = _create_product(admin, sell_price=100.0)
    pid_b = _create_product(admin, sell_price=100.0)
    customer_id = _create_customer(admin)

    r = _sale(admin, [{'product_id': pid_a, 'quantity': 1}, {'product_id': pid_b, 'quantity': 1}],
              customer_id=customer_id, amount_paid=999999)
    assert r.status_code == 200, r.get_json()
    sale = r.get_json()['data']
    assert sale['total'] == 200.0  # earn = int(200/10) = 20

    rr1 = _return(admin, sale['id'], [{'product_id': pid_a, 'quantity': 1}])
    assert rr1.status_code == 200, rr1.get_json()
    return_id_1 = rr1.get_json()['data']['id']

    rr2 = _return(admin, sale['id'], [{'product_id': pid_b, 'quantity': 1}])
    assert rr2.status_code == 200, rr2.get_json()
    return_id_2 = rr2.get_json()['data']['id']

    assert return_id_1 != return_id_2, "setup sanity: two separate returns must get two separate ids"

    adjusts = _adjust_rows(cid, customer_id)
    assert len(adjusts) == 2, adjusts
    by_return_id = {row['return_id']: row for row in adjusts}
    assert set(by_return_id) == {return_id_1, return_id_2}, (
        "each return's own reversal row must carry ITS id, never the OTHER return's", adjusts)
