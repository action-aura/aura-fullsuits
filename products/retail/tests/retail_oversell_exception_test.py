"""
Aura Retail -- schema v20 / stock_exceptions regression coverage: launch-
readiness Phase 7 stage 7d-i, the oversell exception queue. See
docs/launch-readiness/phase7-offline-ux.md "Decision 4",
database/schema.py::_migrate_add_stock_exceptions, and
commercial_runtime/sync/sync_service.py::_record_or_refresh_stock_exception.

DETECTION AND RECORDING ONLY. This stage relaxes no guard and refuses
nothing new: `create_sale`'s `Insufficient stock` check stays exactly as it
was, and every block from stage 7c is untouched. Nothing in this file tests
a refusal changing -- it tests that a negative balance, already reachable in
shipped code with nothing relaxed, now lands somewhere a human can see it.

THE MECHANISM THIS FILE PROVES. `create_sale` refuses `qty > on_hand`
against only THIS device's own local balance, so two tills that each hold
the last unit each pass their own check and each sell it -- both sales are
individually correct. `_apply_event`'s `inventory_movement` branch then
merges the OTHER device's movement onto this device's own balance with no
floor at zero, and it is at THAT merge -- never at either till's own local
sale -- that the balance actually goes negative. So the tests below drive
the "local" half of the scenario through the REAL `/sales` route (a genuine
create_sale, exercising its own real oversell check) and the "merge" half
through `SyncService._apply_event` directly (the real production apply
site, not a hand-inserted negative row) -- matching the technique
retail_stock_sync_apply_hardening_test.py already established for proving
claims about this exact branch.

Self-contained bootstrap, matching retail_stock_accuracy_screen_route_test.py
and retail_route_capability_matrix_test.py (no shared conftest.py exists
here): one Flask app for the whole module, one company per test via the
`company` fixture so exception rows from one test can never leak into
another's assertions.

Run:
    pytest products/retail/tests/retail_oversell_exception_test.py -v
"""
import os
import shutil
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_oversell_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402

seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")
os.environ.pop("AURA_RETAIL_DEMO_MODE", None)

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402
from commercial_runtime.sync.sync_service import SyncService  # noqa: E402
import api.retail_api as _retail_api  # noqa: E402
import database.schema as sch  # noqa: E402
from database.schema import get_retail_conn  # noqa: E402


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


# ── Fixtures / helpers (deliberately duplicated per-file convention -- see
#    the module docstrings this file cites for why nothing here is shared
#    via a conftest.py) ───────────────────────────────────────────────────

def _new_company(role="admin"):
    email = f"oversell-{uuid.uuid4().hex[:10]}@test.local"
    password = "OversellPW1"
    company_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, require_password_change) "
        "VALUES (?,?,?,?,?,?,?,0)",
        (user_id, company_id, "EMP-0001", email, hash_password(password), role, "active"),
    )
    conn.commit()
    conn.close()
    return company_id, email, password


def _login(email, password):
    c = app.test_client()
    r = c.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.get_json()
    c.get("/api/sub/retail/settings/tax")   # forces the lazy schema helpers, same as its sibling files
    return c


@pytest.fixture
def company():
    """One company per test -- exception rows are company-scoped, and a
    shared company would make one test's oversell visible to another's
    'no exception' assertion."""
    company_id, email, password = _new_company()
    return company_id, _login(email, password)


def _create_product(client, initial_stock=0):
    sku = f"OVSL-{uuid.uuid4().hex[:8]}"
    r = client.post("/api/sub/retail/products", json={
        "name": f"Oversell test item {sku}", "sku": sku, "sell_price": 10.0,
        "cost_price": 5.0, "tax_rate": 0, "initial_stock": initial_stock,
    })
    assert r.status_code == 200, r.get_json()
    return r.get_json()["data"]["id"], sku


def _sale(client, product_id, quantity=1):
    """A REAL sale through the REAL route -- this device's own local till
    ringing up its own copy of the last unit, exercising create_sale's own
    genuine `qty > on_hand` check rather than bypassing it."""
    payload = {
        'items': [{'product_id': product_id, 'quantity': quantity}],
        'amount_paid': 100000, 'payment_method': 'cash',
        'idempotency_key': str(uuid.uuid4()),
    }
    return client.post('/api/sub/retail/sales', json=payload)


def _branch_info(company_id):
    """This company's working branch -- the SAME one _create_product's
    initial_stock and _sale() decrement, via _default_branch/_branch_uid
    (api.retail_api's own helpers; not re-derived here so this file can
    never quietly disagree with the routes it is testing about which
    branch a sale actually moved)."""
    conn = get_retail_conn()
    try:
        bid = _retail_api._default_branch(conn, company_id)
        buid = _retail_api._branch_uid(conn, bid)
        conn.commit()
        return bid, buid
    finally:
        conn.close()


def _balance(company_id, product_id, branch_id):
    conn = get_retail_conn()
    try:
        row = conn.execute(
            "SELECT quantity_on_hand FROM inventory_balances WHERE company_id=? AND product_id=? AND branch_id=?",
            (company_id, product_id, branch_id)
        ).fetchone()
        return row['quantity_on_hand'] if row else None
    finally:
        conn.close()


def _exceptions_for(company_id, *, product_id=None):
    conn = get_retail_conn()
    try:
        sql = "SELECT * FROM stock_exceptions WHERE company_id=?"
        params = [company_id]
        if product_id is not None:
            sql += " AND product_id=?"
            params.append(product_id)
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _movement_event(product_id, branch_uid, quantity, movement_type='sale', uid=None):
    return {
        "entity_type": "inventory_movement", "event_type": "create",
        "payload": {
            "uid": uid or str(uuid.uuid4()), "product_id": product_id, "branch_uid": branch_uid,
            "movement_type": movement_type, "quantity": quantity, "unit_cost": 0,
            "reference": "OTHER-DEVICE", "notes": None, "created_by": "System",
            "actor_user_uid": None, "terminal_id": None, "created_at_utc": None,
        },
    }


def _apply_incoming_movement(company_id, product_id, branch_uid, quantity, movement_type='sale'):
    """Simulates ANOTHER device's own inventory_movement arriving through
    the REAL sync apply path -- SyncService._apply_event, the one and only
    site stage 7d-i's writer lives in. This is the "merge" half of every
    two-tills scenario below; the "local" half is always _sale() above,
    never this function -- writing the exception from both places would
    invent a second source for one fact (Decision 4)."""
    conn = get_retail_conn()
    try:
        service = SyncService(client_factory=lambda: None, get_conn=get_retail_conn,
                               local_company_id_provider=lambda: company_id)
        service._apply_event(conn, _movement_event(product_id, branch_uid, quantity, movement_type),
                              local_company_id=company_id)
        conn.commit()
    finally:
        conn.close()


# ═════════════════════════════════════════════════════════════════════════
# 1. The scenario the writer exists for
# ═════════════════════════════════════════════════════════════════════════

def test_two_tills_each_selling_the_last_unit_records_one_exception(company):
    company_id, client = company
    product_id, _sku = _create_product(client, initial_stock=1)
    bid, buid = _branch_info(company_id)

    # Device A (this device): its OWN local sale of the last unit, through
    # the real route. Its own check passes -- on_hand is genuinely 1 here.
    r = _sale(client, product_id)
    assert r.status_code == 200, r.get_json()
    assert _balance(company_id, product_id, bid) == 0

    # Device B: its OWN sale of the SAME product, arriving now as a pulled
    # sync event. B's own check also passed, against B's own copy of the
    # same starting balance -- this device never saw B's sale coming.
    _apply_incoming_movement(company_id, product_id, buid, quantity=-1)

    assert _balance(company_id, product_id, bid) == -1, (
        "the merge did not actually cross into negative -- the fixture is not "
        "the scenario this test claims to be driving"
    )

    rows = _exceptions_for(company_id, product_id=product_id)
    assert len(rows) == 1, f"expected exactly one exception, found {len(rows)}: {rows}"
    row = rows[0]
    assert row['company_id'] == company_id
    assert row['branch_id'] == bid
    assert row['observed_quantity_on_hand'] == -1
    assert row['resolved_at_utc'] is None
    assert row['detected_at_utc']


# ═════════════════════════════════════════════════════════════════════════
# 2. One open row per key, refreshed rather than duplicated
# ═════════════════════════════════════════════════════════════════════════

def test_a_further_negative_merge_updates_the_open_exception_instead_of_adding_a_second(company):
    company_id, client = company
    product_id, _sku = _create_product(client, initial_stock=1)
    bid, buid = _branch_info(company_id)
    assert _sale(client, product_id).status_code == 200
    _apply_incoming_movement(company_id, product_id, buid, quantity=-1)

    first = _exceptions_for(company_id, product_id=product_id)
    assert len(first) == 1, first
    first_id = first[0]['id']
    first_detected = first[0]['detected_at_utc']

    # A THIRD device's own sale of the same product/branch, merging in
    # later and pushing this device's balance further negative.
    _apply_incoming_movement(company_id, product_id, buid, quantity=-1)
    assert _balance(company_id, product_id, bid) == -2

    second = _exceptions_for(company_id, product_id=product_id)
    assert len(second) == 1, (
        f"a further negative merge appended a SECOND exception instead of refreshing the open "
        f"one -- a shop with one genuinely oversold product would fill this queue with one row "
        f"per sync tick: {second}"
    )
    assert second[0]['id'] == first_id, "the refresh created a new row instead of updating the existing one"
    assert second[0]['observed_quantity_on_hand'] == -2
    assert second[0]['detected_at_utc'] != first_detected, (
        "detected_at_utc was not refreshed by the further negative merge"
    )


# ═════════════════════════════════════════════════════════════════════════
# 3. The rowcount gate
# ═════════════════════════════════════════════════════════════════════════

def test_a_replayed_movement_does_not_record_a_second_exception(company):
    company_id, client = company
    product_id, _sku = _create_product(client, initial_stock=1)
    bid, buid = _branch_info(company_id)
    assert _sale(client, product_id).status_code == 200

    service = SyncService(client_factory=lambda: None, get_conn=get_retail_conn,
                           local_company_id_provider=lambda: company_id)
    ev = _movement_event(product_id, buid, quantity=-1)  # ONE event, applied twice below

    conn = get_retail_conn()
    try:
        service._apply_event(conn, ev, local_company_id=company_id)
        conn.commit()
    finally:
        conn.close()

    first = _exceptions_for(company_id, product_id=product_id)
    assert len(first) == 1, first
    assert first[0]['observed_quantity_on_hand'] == -1

    # The SAME event, replayed -- a re-pulled cursor range, a retried
    # quarantine row, or the relay re-sending the same batch.
    # `ON CONFLICT(uid) ... DO NOTHING` means cur.rowcount is 0 this time,
    # and nothing about the shop's stock actually changes on this call.
    conn = get_retail_conn()
    try:
        service._apply_event(conn, ev, local_company_id=company_id)
        conn.commit()
    finally:
        conn.close()

    assert _balance(company_id, product_id, bid) == -1, "the replay double-applied the movement"

    second = _exceptions_for(company_id, product_id=product_id)
    assert len(second) == 1, f"a replayed movement recorded a second exception: {second}"
    assert second[0]['id'] == first[0]['id']
    assert second[0]['observed_quantity_on_hand'] == first[0]['observed_quantity_on_hand']
    assert second[0]['detected_at_utc'] == first[0]['detected_at_utc'], (
        "the replay re-stamped detected_at_utc even though the movement insert's cur.rowcount "
        "was 0 -- the exception writer is not actually gated on rowcount"
    )


# ═════════════════════════════════════════════════════════════════════════
# 4. The ALLOW half -- a healthy merge writes nothing
# ═════════════════════════════════════════════════════════════════════════

def test_a_healthy_merge_records_no_exception(company):
    company_id, client = company
    product_id, _sku = _create_product(client, initial_stock=10)
    bid, buid = _branch_info(company_id)

    _apply_incoming_movement(company_id, product_id, buid, quantity=-3)
    assert _balance(company_id, product_id, bid) == 7

    rows = _exceptions_for(company_id, product_id=product_id)
    assert rows == [], (
        f"a healthy (non-negative) merge recorded an exception -- an implementation that fires "
        f"on every merge, not only negative ones, would fill the queue with noise: {rows}"
    )


# ═════════════════════════════════════════════════════════════════════════
# 5. Zero is not negative, and the local path is not a writer
# ═════════════════════════════════════════════════════════════════════════

def test_a_local_sale_that_empties_stock_records_no_exception(company):
    company_id, client = company
    product_id, _sku = _create_product(client, initial_stock=1)
    bid, _buid = _branch_info(company_id)

    r = _sale(client, product_id)
    assert r.status_code == 200, r.get_json()
    assert _balance(company_id, product_id, bid) == 0, "the fixture did not actually empty stock"

    rows = _exceptions_for(company_id, product_id=product_id)
    assert rows == [], (
        f"a local sale landing exactly on zero recorded an exception -- zero is not negative, "
        f"and create_sale is not a writer for this table: {rows}"
    )


# ═════════════════════════════════════════════════════════════════════════
# 6. Never auto-resolved
# ═════════════════════════════════════════════════════════════════════════

def test_an_exception_is_never_auto_resolved(company):
    company_id, client = company
    product_id, _sku = _create_product(client, initial_stock=1)
    bid, buid = _branch_info(company_id)
    assert _sale(client, product_id).status_code == 200
    _apply_incoming_movement(company_id, product_id, buid, quantity=-1)

    before = _exceptions_for(company_id, product_id=product_id)
    assert len(before) == 1, before
    assert before[0]['resolved_at_utc'] is None

    # Further syncs, INCLUDING ones that bring the balance back to positive
    # -- a replenishment merged in from another device, then a healthy
    # restock well past zero.
    _apply_incoming_movement(company_id, product_id, buid, quantity=1, movement_type='purchase_in')
    _apply_incoming_movement(company_id, product_id, buid, quantity=10, movement_type='purchase_in')
    assert _balance(company_id, product_id, bid) == 10, "the recovery movements did not actually land"

    after = _exceptions_for(company_id, product_id=product_id)
    assert len(after) == 1, f"the recovered balance changed how many exception rows exist: {after}"
    assert after[0]['id'] == before[0]['id']
    assert after[0]['resolved_at_utc'] is None, (
        "the exception was auto-resolved once the balance recovered -- the oversell already "
        "happened and a human still needs to see it, matching Decision 4's 'resolved by a human "
        "or stays open' rule"
    )


# ═════════════════════════════════════════════════════════════════════════
# 7. The read path
# ═════════════════════════════════════════════════════════════════════════

def test_the_read_path_lists_open_exceptions_with_their_context(company):
    company_id, client = company
    product_id, _sku = _create_product(client, initial_stock=1)
    bid, buid = _branch_info(company_id)
    assert _sale(client, product_id).status_code == 200
    _apply_incoming_movement(company_id, product_id, buid, quantity=-1)

    # A RESOLVED exception for a DIFFERENT product in the SAME company must
    # never appear in this list. Stage 7d-i ships no resolution route at
    # all, so this is written directly -- it is exercising the read
    # route's own filter, not a resolution flow.
    other_id, _sku2 = _create_product(client, initial_stock=0)
    conn = get_retail_conn()
    conn.execute(
        "INSERT INTO stock_exceptions (id, company_id, product_id, branch_id, "
        "observed_quantity_on_hand, detected_at_utc, resolved_at_utc) VALUES (?,?,?,?,?,?,?)",
        (str(uuid.uuid4()), company_id, other_id, bid, -5.0,
         '2026-01-01T00:00:00+00:00', '2026-01-02T00:00:00+00:00'),
    )
    conn.commit()
    conn.close()

    r = client.get('/api/sub/retail/inventory/stock-exceptions')
    assert r.status_code == 200, r.get_json()
    data = r.get_json()['data']
    assert data['count'] == 1, data
    assert len(data['exceptions']) == 1, data

    row = data['exceptions'][0]
    assert row['product_id'] == product_id
    assert row['product_name'], "the read path did not join the product's name"
    assert row['branch_id'] == bid
    assert row['branch_name'], "the read path did not join the branch's name"
    assert row['observed_quantity_on_hand'] == -1
    assert row['detected_at_utc']

    assert other_id not in {e['product_id'] for e in data['exceptions']}, (
        "a RESOLVED exception was listed as open"
    )


# ═════════════════════════════════════════════════════════════════════════
# 8. Schema v20 itself
# ═════════════════════════════════════════════════════════════════════════

def test_v20_migration_is_idempotent_and_lands_on_head(company):
    """The real migration, run twice, against a real database that already
    holds business data -- exactly the situation ensure_schema_version's
    idempotent-guard contract exists to make safe. Self-contained (builds
    its own oversold row rather than relying on another test's leftovers)
    so it stands alone under `-k` or reordering."""
    from commercial_runtime.security.migration_safety import ensure_schema_version

    company_id, client = company
    product_id, _sku = _create_product(client, initial_stock=1)
    bid, buid = _branch_info(company_id)
    assert _sale(client, product_id).status_code == 200
    _apply_incoming_movement(company_id, product_id, buid, quantity=-1)
    assert len(_exceptions_for(company_id, product_id=product_id)) == 1

    def _user_version():
        conn = get_retail_conn()
        try:
            return conn.execute('PRAGMA user_version').fetchone()[0]
        finally:
            conn.close()

    db_path = str(DATA / "database" / "subsystems" / "retail.db")
    assert _user_version() == sch.RETAIL_SCHEMA_VERSION, (
        f"expected the live database to already be at RETAIL_SCHEMA_VERSION "
        f"({sch.RETAIL_SCHEMA_VERSION}); found {_user_version()}"
    )

    # Rewind one version behind head and run the WHOLE migration chain
    # again -- ensure_schema_version only knows "behind", not "behind by
    # one" -- the same technique retail_sync_freshness_test.py's own
    # test_v18_migration_is_idempotent_and_lands_on_head uses, generalised
    # so it never hardcodes 20.
    conn = get_retail_conn()
    try:
        conn.execute(f'PRAGMA user_version = {sch.RETAIL_SCHEMA_VERSION - 1}')
        conn.commit()
    finally:
        conn.close()

    conn = get_retail_conn()
    try:
        ensure_schema_version(conn, db_path, sch.RETAIL_SCHEMA_VERSION,
                               sch._migrate_retail_schema,
                               backup_dir=os.path.join(sch.BASE_DIR, 'migration_backups'))
    finally:
        conn.close()

    assert _user_version() == sch.RETAIL_SCHEMA_VERSION, (
        f"a second migration pass did not land back on RETAIL_SCHEMA_VERSION "
        f"({sch.RETAIL_SCHEMA_VERSION}); got {_user_version()}"
    )

    conn = get_retail_conn()
    try:
        integrity = conn.execute('PRAGMA integrity_check').fetchone()[0]
        rows = conn.execute(
            "SELECT id, resolved_at_utc FROM stock_exceptions WHERE company_id=? AND product_id=?",
            (company_id, product_id)
        ).fetchall()
    finally:
        conn.close()
    assert integrity == 'ok', integrity
    assert len(rows) == 1, (
        f"the re-run migration duplicated or dropped this test's own exception row: {rows}"
    )
    assert rows[0]['resolved_at_utc'] is None
