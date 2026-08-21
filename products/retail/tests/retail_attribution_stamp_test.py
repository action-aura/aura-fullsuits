"""
Aura Retail -- end-to-end proof that every write stamps the schema-v13
attribution columns (launch-readiness Phase 2).

`retail_attribution_stamp_structural_test.py` is the companion to this file and
proves the SQL names the columns. Naming them is not the same as filling them:
a column list can carry `actor_user_uid` and bind `None` to it, or bind a value
of the wrong KIND, and the source scan is blind to both. This file drives the
real Flask routes against a real retail.db and reads the rows back.

THE THREE THINGS THAT ARE EASY TO GET WRONG HERE, all pinned below:

1. `actor_user_uid` must be the registry's `users.uid` -- the WIRE identity --
   and NOT `users.id`. The session carries `mt_user_id`, which
   mt_auth.create_session sets to `user['id']` (the LOCAL primary key, which
   registry v3's account_schema.py docstring calls "a private detail of THIS
   install"). Both are uuid4 strings. Stamping the wrong one is undetectable
   locally, passes `uuid.UUID()`, passes every eye test, and only surfaces when
   a peer device tries to resolve the actor and finds nobody.

2. `created_at_utc` must come from an aware UTC clock. `create_sale` and
   `create_return` write LOCAL wall clock into `created_at` on purpose (each
   has a comment saying why -- the dashboard buckets by local date). Reusing
   that same value for `created_at_utc` would produce a column that is wrong by
   this machine's offset while looking perfectly well-formed, which is the
   exact bug the column was added to end.

3. `uid` must be a real RFC-4122 uuid4. The v13 migration's own docstring
   records why: SQLite's `lower(hex(randomblob(16)))` produces a 32-char string
   that `uuid.UUID()` happily ACCEPTS, so a test that only parses the value
   would pass a broken implementation. Version and canonical form are both
   asserted.

Run:
    pytest products/retail/tests/retail_attribution_stamp_test.py -v
"""
import io
import os
import shutil
import sys
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_attribution_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402
seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")
os.environ.pop("AURA_RETAIL_DEMO_MODE", None)

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from commercial_runtime.identity import device_context  # noqa: E402
from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402
from database.schema import get_retail_conn  # noqa: E402

# Establish this install's device identity ONCE, up front, the same way a real
# first launch does. `local_terminal_id()` reads it with the non-creating
# `peek_*` variant on purpose (stamping a row must never manufacture identity
# as a side effect), so without this line every terminal_id below would be a
# legitimate NULL and the assertions would be asserting nothing.
TERMINAL_ID = device_context.local_device_uuid()


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


# ── assertion helpers ────────────────────────────────────────────────────────

def _assert_uuid4(value, label):
    """A real RFC-4122 v4 UUID in canonical form.

    Three assertions, not one, because the obvious single assertion is not
    enough. `uuid.UUID('001e5d251085672d17b897bf5b3eb327')` -- the 32-char
    output of SQLite's `lower(hex(randomblob(16)))` -- PARSES FINE. It fails
    only on `.version == 4` and on comparison against the canonical dashed
    form. The v13 migration test learned this the same way; repeated here
    rather than re-learned, because these write sites are the other half of
    the same wire contract (Owner's sync ingest gates on `uuid.UUID(...)`).
    """
    assert value, f"{label} is NULL/empty -- the row was written unattributed"
    parsed = uuid.UUID(str(value))
    assert parsed.version == 4, f"{label} {value!r} is a v{parsed.version} UUID, not uuid4"
    assert str(parsed) == str(value), (
        f"{label} {value!r} is not canonical uuid4 text (hex(randomblob(16)) "
        f"produces exactly this shape and is rejected on the wire)"
    )


def _assert_utc(value, label):
    """Timezone-AWARE UTC, close to now.

    The offset assertion is the machine-independent half: a value produced by
    `datetime.now().strftime('%Y-%m-%d %H:%M:%S')` carries no tzinfo at all, so
    it fails here on a UTC+0 CI box exactly as it does on a UTC+3 dev box. The
    freshness window is the second, weaker half -- it catches a value that is
    aware but wrong (e.g. an offset applied twice).
    """
    assert value, f"{label} is NULL/empty -- the row has no real-time anchor"
    parsed = datetime.fromisoformat(str(value))
    assert parsed.tzinfo is not None, (
        f"{label} {value!r} is a NAIVE timestamp. A naive string in a *_at_utc "
        f"column is indistinguishable from local wall clock, which is the "
        f"precise defect this column exists to fix"
    )
    assert parsed.utcoffset() == timedelta(0), (
        f"{label} {value!r} carries offset {parsed.utcoffset()}, not UTC"
    )
    drift = abs(datetime.now(timezone.utc) - parsed)
    assert drift < timedelta(minutes=5), (
        f"{label} {value!r} is {drift} away from now -- an aware timestamp "
        f"built from the wrong clock"
    )


def _assert_stamped(row, label, user_uid, uid_expected=True):
    """The full v13 actor stamp on one row."""
    assert row['actor_user_uid'] == user_uid, (
        f"{label}.actor_user_uid is {row['actor_user_uid']!r}, expected the "
        f"signed-in user's registry `users.uid` {user_uid!r}"
    )
    assert row['terminal_id'] == TERMINAL_ID, (
        f"{label}.terminal_id is {row['terminal_id']!r}, expected this "
        f"install's device uuid {TERMINAL_ID!r} (the value device_registry "
        f"keys `devices.id` by, so the two can be joined)"
    )
    _assert_utc(row['created_at_utc'], f"{label}.created_at_utc")
    if uid_expected:
        _assert_uuid4(row['uid'], f"{label}.uid")


# ── fixture ──────────────────────────────────────────────────────────────────

def _make_shop(stock=500):
    """A signed-in admin with a branch, a supplier and one stocked product.

    The `users` row is inserted WITH a `uid`, which is what production always
    has: `onboarding_routes.create_admin`/`create_employee` write it inline,
    and registry v3's `_backfill_uids` stamps any row that predates them on the
    next launch. Fixtures elsewhere in this suite omit it because nothing they
    assert on reads it -- here it is the value under test, so leaving it NULL
    would make every actor assertion below vacuous.
    """
    email = f"attr-{uuid.uuid4().hex[:10]}@test.local"
    password = "AttributionPW1"
    company_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    user_uid = str(uuid.uuid4())

    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, uid, company_id, employee_id, email, password_hash, role, status, "
        "require_password_change) VALUES (?,?,?,?,?,?,?,?,0)",
        (user_id, user_uid, company_id, "EMP-0001", email, hash_password(password), "admin", "active"),
    )
    conn.commit()
    conn.close()

    rconn = get_retail_conn()
    rconn.execute("INSERT INTO branches (company_id,name) VALUES (?, 'Main')", (company_id,))
    branch_id = rconn.execute(
        "SELECT id FROM branches WHERE company_id=? ORDER BY id LIMIT 1", (company_id,)
    ).fetchone()[0]
    product_id = str(uuid.uuid4())
    rconn.execute(
        "INSERT INTO products (id,company_id,sku,name,cost_price,sell_price,tax_rate) "
        "VALUES (?,?,'ATTR-1','Attribution Item',5,100,0)",
        (product_id, company_id),
    )
    supplier_id = str(uuid.uuid4())
    rconn.execute("INSERT INTO suppliers (id,company_id,name) VALUES (?,?,'Attr Supplier')",
                  (supplier_id, company_id))
    rconn.execute(
        "INSERT INTO inventory_balances (company_id,product_id,branch_id,quantity_on_hand) VALUES (?,?,?,?)",
        (company_id, product_id, branch_id, stock),
    )
    rconn.commit()
    rconn.close()

    client = app.test_client()
    client.post("/api/auth/login", json={"email": email, "password": password})
    client.get("/api/sub/retail/settings/tax")  # runs _ensure_credit_schema / doc_sequences

    # sale_number / return_number carry BARE unique constraints while
    # _next_ref()'s counter restarts per company, so every fresh company in
    # this shared temp DB would otherwise collide on its first document. Same
    # random reseed the financial-authority and pricing suites use.
    import random as _random
    dconn = get_retail_conn()
    for doc_type in ('sale', 'return', 'po', 'receipt', 'supplier_payment'):
        dconn.execute(
            "INSERT OR REPLACE INTO doc_sequences (company_id,doc_type,last_no) VALUES (?,?,?)",
            (company_id, doc_type, _random.randint(1, 5_000_000)),
        )
    dconn.commit()
    dconn.close()

    return {
        'client': client, 'company_id': company_id, 'branch_id': branch_id,
        'product_id': product_id, 'supplier_id': supplier_id,
        'user_id': user_id, 'user_uid': user_uid,
    }


def _sell(shop, qty=2):
    r = shop['client'].post('/api/sub/retail/sales', json={
        'items': [{'product_id': shop['product_id'], 'quantity': qty}],
        'payment_method': 'cash', 'amount_paid': 100000,
        'idempotency_key': str(uuid.uuid4()),
    })
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()['data']


# ── sales ────────────────────────────────────────────────────────────────────

def test_a_sale_stamps_the_sale_its_lines_and_its_stock_movements():
    """The busiest write path in the product, and the one whose missing
    attribution is felt first: "who rang this?"."""
    shop = _make_shop()
    sale = _sell(shop, qty=3)

    conn = get_retail_conn()
    row = conn.execute("SELECT * FROM sales WHERE id=?", (sale['id'],)).fetchone()
    _assert_stamped(row, 'sales', shop['user_uid'])

    items = conn.execute("SELECT * FROM sale_items WHERE sale_id=?", (sale['id'],)).fetchall()
    assert items, 'the sale wrote no lines'
    for i, item in enumerate(items):
        # sale_items is a uid-bearing table but NOT an actor table (it has no
        # company_id either -- line tables scope through their parent). Only
        # the wire identity is expected here, exactly as RETAIL_UID_TABLES /
        # RETAIL_ACTOR_TABLES divide them.
        _assert_uuid4(item['uid'], f'sale_items[{i}].uid')

    movements = conn.execute(
        "SELECT * FROM inventory_movements WHERE company_id=? AND reference=?",
        (shop['company_id'], sale['sale_number'])
    ).fetchall()
    assert movements, 'the sale decremented stock without writing a ledger row'
    for i, mv in enumerate(movements):
        _assert_stamped(mv, f'inventory_movements[{i}] (sale_out)', shop['user_uid'])
    conn.close()


def test_the_payment_a_sale_records_carries_a_wire_identity():
    """`payments` is uid-bearing but not actor-bearing (it has carried its own
    free-text `created_by` since the AR/AP ledger shipped). The sale's retained
    cash goes through _record_payment, which is also the helper every customer/
    supplier/PO payment route funnels through -- so one assertion here covers
    every money-movement row in the product."""
    shop = _make_shop()
    sale = _sell(shop)
    conn = get_retail_conn()
    pay = conn.execute(
        "SELECT * FROM payments WHERE company_id=? AND related_type='sale' AND related_id=?",
        (shop['company_id'], sale['id'])
    ).fetchone()
    conn.close()
    assert pay is not None, 'the sale retained cash but wrote no payments row'
    _assert_uuid4(pay['uid'], 'payments.uid')


def test_two_sales_never_share_a_uid():
    """The whole point of `uid` is that it is unique across devices, and the
    v13 unique index only enforces that for rows that actually differ. A writer
    that computed one uid and reused it for every row would satisfy every
    not-null assertion above."""
    shop = _make_shop()
    first = _sell(shop)
    second = _sell(shop)
    conn = get_retail_conn()
    uids = [r['uid'] for r in conn.execute(
        "SELECT uid FROM sales WHERE id IN (?,?)", (first['id'], second['id'])).fetchall()]
    line_uids = [r['uid'] for r in conn.execute(
        "SELECT uid FROM sale_items WHERE sale_id IN (?,?)", (first['id'], second['id'])).fetchall()]
    conn.close()
    assert len(set(uids)) == 2, f'two sales share a uid: {uids}'
    assert len(set(line_uids)) == len(line_uids), f'sale_items reuse a uid: {line_uids}'


def test_created_at_utc_is_a_different_instant_from_the_local_created_at():
    """`created_at` stays local on purpose and `created_at_utc` must not simply
    copy it.

    Asserted as "not the same string" rather than "differs by N hours" so it
    holds on a UTC+0 runner too: the aware ISO form ('...T...+00:00') can never
    equal the naive '%Y-%m-%d %H:%M:%S' form regardless of offset. The offset
    itself is asserted in _assert_utc.
    """
    shop = _make_shop()
    sale = _sell(shop)
    conn = get_retail_conn()
    row = conn.execute("SELECT created_at, created_at_utc FROM sales WHERE id=?",
                       (sale['id'],)).fetchone()
    conn.close()
    assert row['created_at'], 'the local created_at stamp was lost'
    assert row['created_at_utc'] != row['created_at'], (
        f"created_at_utc ({row['created_at_utc']!r}) is byte-identical to the "
        f"LOCAL created_at -- local wall clock has been filed as UTC"
    )
    _assert_utc(row['created_at_utc'], 'sales.created_at_utc')


def test_actor_user_uid_is_the_wire_uid_and_never_the_local_user_id():
    """The single most plausible wrong answer, called out explicitly.

    `_uid()` -- the helper every `cashier`/`created_by` column in this file
    already uses -- returns `session['mt_user_id']`, which is `users.id`. It is
    a uuid4 string, so dropping it into `actor_user_uid` looks completely
    correct until a second device tries to resolve it.
    """
    shop = _make_shop()
    sale = _sell(shop)
    conn = get_retail_conn()
    row = conn.execute("SELECT actor_user_uid, cashier FROM sales WHERE id=?",
                       (sale['id'],)).fetchone()
    conn.close()
    assert row['actor_user_uid'] == shop['user_uid']
    assert row['actor_user_uid'] != shop['user_id'], (
        "actor_user_uid holds the LOCAL users.id. Both are uuid4 strings, so "
        "nothing local can tell them apart -- but a peer device names users by "
        "users.uid and will resolve this to nobody"
    )
    # And the pre-existing free-text trail is untouched: v13 adds a second
    # channel beside `cashier`, it never rewrites the first one.
    assert row['cashier'] == shop['user_id']


# ── returns ──────────────────────────────────────────────────────────────────

def test_a_return_stamps_the_return_its_lines_and_its_restock_movements():
    shop = _make_shop()
    sale = _sell(shop, qty=4)
    r = shop['client'].post('/api/sub/retail/returns', json={
        'sale_id': sale['id'],
        'items': [{'product_id': shop['product_id'], 'quantity': 2}],
        'idempotency_key': str(uuid.uuid4()),
    })
    assert r.status_code == 200, r.get_data(as_text=True)
    ret = r.get_json()['data']

    conn = get_retail_conn()
    row = conn.execute("SELECT * FROM returns WHERE id=?", (ret['id'],)).fetchone()
    _assert_stamped(row, 'returns', shop['user_uid'])

    lines = conn.execute("SELECT * FROM return_items WHERE return_id=?", (ret['id'],)).fetchall()
    assert lines
    for i, line in enumerate(lines):
        _assert_uuid4(line['uid'], f'return_items[{i}].uid')

    movements = conn.execute(
        "SELECT * FROM inventory_movements WHERE company_id=? AND reference=?",
        (shop['company_id'], ret['return_number'])
    ).fetchall()
    assert movements, 'the return restocked without writing a ledger row'
    for i, mv in enumerate(movements):
        _assert_stamped(mv, f'inventory_movements[{i}] (return_in)', shop['user_uid'])
    conn.close()


# ── every other inventory_movements writer ───────────────────────────────────

def test_a_manual_stock_adjustment_stamps_its_movement():
    """The one movement type with no document behind it -- `reference='ADJ'`,
    no sale, no return, no PO. If any write path in this product needs to say
    who did it, it is this one."""
    shop = _make_shop()
    r = shop['client'].post(f"/api/sub/retail/products/{shop['product_id']}/stock-adjust",
                            json={'quantity': -5, 'reason': 'Damaged in transit'})
    assert r.status_code == 200, r.get_data(as_text=True)
    conn = get_retail_conn()
    mv = conn.execute(
        "SELECT * FROM inventory_movements WHERE company_id=? AND product_id=? AND reference='ADJ' "
        "ORDER BY id DESC LIMIT 1", (shop['company_id'], shop['product_id'])).fetchone()
    conn.close()
    assert mv is not None, 'the adjustment moved a balance without a ledger row'
    _assert_stamped(mv, 'inventory_movements (ADJ)', shop['user_uid'])


def test_creating_a_product_with_opening_stock_stamps_its_movement():
    shop = _make_shop()
    r = shop['client'].post('/api/sub/retail/products', json={
        'sku': f'OPEN-{uuid.uuid4().hex[:8]}', 'name': 'Opening Stock Item',
        'sell_price': 10, 'initial_stock': 12,
    })
    assert r.status_code in (200, 201), r.get_data(as_text=True)
    new_pid = r.get_json().get('data', {}).get('id') or r.get_json().get('id')
    conn = get_retail_conn()
    mv = conn.execute(
        "SELECT * FROM inventory_movements WHERE company_id=? AND product_id=? "
        "AND movement_type='opening_stock'", (shop['company_id'], new_pid)).fetchone()
    conn.close()
    assert mv is not None, 'opening stock was filed without a ledger row'
    _assert_stamped(mv, 'inventory_movements (opening_stock)', shop['user_uid'])


def test_receiving_a_purchase_order_stamps_its_movements():
    shop = _make_shop()
    r = shop['client'].post('/api/sub/retail/purchase-orders', json={
        'supplier_id': shop['supplier_id'],
        'items': [{'product_id': shop['product_id'], 'quantity': 7, 'unit_cost': 4}],
    })
    assert r.status_code == 200, r.get_data(as_text=True)
    po = r.get_json()['data']
    r = shop['client'].post(f"/api/sub/retail/purchase-orders/{po['id']}/receive")
    assert r.status_code == 200, r.get_data(as_text=True)

    conn = get_retail_conn()
    movements = conn.execute(
        "SELECT * FROM inventory_movements WHERE company_id=? AND reference=?",
        (shop['company_id'], po['po_number'])).fetchall()
    conn.close()
    assert movements, 'the PO receipt added stock without a ledger row'
    for i, mv in enumerate(movements):
        _assert_stamped(mv, f'inventory_movements[{i}] (purchase_in)', shop['user_uid'])


# ── cash drawer ──────────────────────────────────────────────────────────────

def test_opening_a_cash_session_stamps_it():
    """cash_sessions is an actor table but NOT a uid table -- its `id` is
    already a client-generated uuid4 (schema v10), so it needs no second wire
    identity. The trio still applies: a drawer with no accountable operator is
    the whole reason shift management exists."""
    shop = _make_shop()
    r = shop['client'].post('/api/sub/retail/cash-sessions/open', json={'opening_float': 100})
    assert r.status_code == 200, r.get_data(as_text=True)
    sid = r.get_json()['data']['id']
    conn = get_retail_conn()
    row = conn.execute("SELECT * FROM cash_sessions WHERE id=?", (sid,)).fetchone()
    conn.close()
    _assert_stamped(row, 'cash_sessions', shop['user_uid'], uid_expected=False)
    assert row['opened_by'] == shop['user_id'], 'the legacy opened_by trail was disturbed'


def test_a_cash_movement_stamps_it():
    shop = _make_shop()
    r = shop['client'].post('/api/sub/retail/cash-sessions/open', json={'opening_float': 50})
    assert r.status_code == 200, r.get_data(as_text=True)
    sid = r.get_json()['data']['id']
    r = shop['client'].post(f'/api/sub/retail/cash-sessions/{sid}/movements',
                            json={'type': 'paid_out', 'amount': 20, 'reason': 'Window cleaner'})
    assert r.status_code == 200, r.get_data(as_text=True)
    mid = r.get_json()['data']['id']
    conn = get_retail_conn()
    row = conn.execute("SELECT * FROM cash_movements WHERE id=?", (mid,)).fetchone()
    conn.close()
    _assert_stamped(row, 'cash_movements', shop['user_uid'], uid_expected=False)


# ── branches ─────────────────────────────────────────────────────────────────

def test_creating_a_branch_gives_it_a_wire_identity():
    shop = _make_shop()
    r = shop['client'].post('/api/sub/retail/branches', json={'name': 'Second Branch'})
    assert r.status_code == 200, r.get_data(as_text=True)
    bid = r.get_json()['data']['id']
    conn = get_retail_conn()
    row = conn.execute("SELECT * FROM branches WHERE id=?", (bid,)).fetchone()
    conn.close()
    _assert_uuid4(row['uid'], 'branches.uid')


def test_the_self_healing_default_branch_gives_itself_a_wire_identity():
    """`_default_branch` invents a branch when a company has none, from inside
    whatever route happened to need one. It is the easiest write site in the
    file to overlook -- it is four lines long, in a helper, and nothing about
    the calling route mentions branches."""
    shop = _make_shop()
    conn = get_retail_conn()
    conn.execute("DELETE FROM branches WHERE company_id=?", (shop['company_id'],))
    conn.commit()
    conn.close()

    r = shop['client'].post('/api/sub/retail/products', json={
        'sku': f'HEAL-{uuid.uuid4().hex[:8]}', 'name': 'Branch Healer', 'sell_price': 1,
    })
    assert r.status_code in (200, 201), r.get_data(as_text=True)

    conn = get_retail_conn()
    row = conn.execute("SELECT * FROM branches WHERE company_id=?", (shop['company_id'],)).fetchone()
    conn.close()
    assert row is not None, '_default_branch did not self-heal'
    _assert_uuid4(row['uid'], 'branches.uid (self-healed by _default_branch)')


# ── bulk import ──────────────────────────────────────────────────────────────

def _import(shop, entity, csv_text, mapping):
    return shop['client'].post(
        '/api/import/execute',
        data={
            'file': (io.BytesIO(csv_text.encode('utf-8')), 'import.csv'),
            'system': 'retail', 'entity': entity,
            'mapping': __import__('json').dumps(mapping),
        },
        content_type='multipart/form-data',
    )


def test_a_bulk_product_import_stamps_the_stock_movements_it_posts():
    """A single upload can restate the whole catalogue's opening stock, which
    makes this by some distance the highest-leverage writer into
    inventory_movements -- and the one furthest from anyone watching."""
    shop = _make_shop()
    sku = f'IMP-{uuid.uuid4().hex[:8]}'
    # sell_price is a REQUIRED field of the retail/products import schema; a
    # row missing it is dropped by _clean_records before any handler sees it.
    r = _import(shop, 'products',
                f"sku,name,sell_price,initial_stock\n{sku},Imported Item,9.50,25\n",
                {'sku': 'sku', 'name': 'name', 'sell_price': 'sell_price',
                 'initial_stock': 'initial_stock'})
    assert r.status_code == 200, r.get_data(as_text=True)

    conn = get_retail_conn()
    pid = conn.execute("SELECT id FROM products WHERE company_id=? AND sku=?",
                       (shop['company_id'], sku)).fetchone()
    assert pid is not None, r.get_data(as_text=True)
    mv = conn.execute(
        "SELECT * FROM inventory_movements WHERE company_id=? AND product_id=?",
        (shop['company_id'], pid['id'])).fetchone()
    conn.close()
    assert mv is not None, 'the import declared opening stock with no ledger row'
    _assert_stamped(mv, 'inventory_movements (import)', shop['user_uid'])


def test_a_bulk_branch_import_gives_each_branch_a_wire_identity():
    shop = _make_shop()
    name = f'Imported Branch {uuid.uuid4().hex[:6]}'
    r = _import(shop, 'branches', f"name,address\n{name},12 High Street\n",
                {'name': 'name', 'address': 'address'})
    assert r.status_code == 200, r.get_data(as_text=True)
    conn = get_retail_conn()
    row = conn.execute("SELECT * FROM branches WHERE company_id=? AND name=?",
                       (shop['company_id'], name)).fetchone()
    conn.close()
    assert row is not None, r.get_data(as_text=True)
    _assert_uuid4(row['uid'], 'branches.uid (bulk import)')


def test_an_import_into_a_company_with_no_branch_stamps_the_branch_it_invents():
    """_handle_retail_products has its OWN self-healing branch insert, separate
    from retail_api's _default_branch -- a second copy of the same easily
    missed write."""
    shop = _make_shop()
    conn = get_retail_conn()
    conn.execute("DELETE FROM branches WHERE company_id=?", (shop['company_id'],))
    conn.commit()
    conn.close()

    sku = f'IMPB-{uuid.uuid4().hex[:8]}'
    r = _import(shop, 'products', f"sku,name,sell_price,initial_stock\n{sku},Healer,3.25,5\n",
                {'sku': 'sku', 'name': 'name', 'sell_price': 'sell_price',
                 'initial_stock': 'initial_stock'})
    assert r.status_code == 200, r.get_data(as_text=True)

    conn = get_retail_conn()
    row = conn.execute("SELECT * FROM branches WHERE company_id=?", (shop['company_id'],)).fetchone()
    conn.close()
    assert row is not None, 'the import did not self-heal a branch'
    _assert_uuid4(row['uid'], 'branches.uid (self-healed by the importer)')


# ── degradation ──────────────────────────────────────────────────────────────

def test_an_unresolvable_terminal_identity_leaves_the_column_null_but_never_refuses_the_sale():
    """Attribution is bookkeeping; a sale is the business.

    `local_terminal_id()` is documented as returning None rather than raising
    on a corrupt local-device record, precisely so an identity problem cannot
    stop a shop trading -- "an unattributed row is recoverable; a refused sale
    is not". This pins the write sites to the same posture: the stamp degrades
    to NULL, the transaction still commits, and the response is unchanged.
    """
    shop = _make_shop()
    original = device_context.peek_local_device_uuid

    def _explode():
        raise device_context.LocalDeviceStateCorruptError('simulated corrupt local_device.json')

    device_context.peek_local_device_uuid = _explode
    try:
        sale = _sell(shop)
    finally:
        device_context.peek_local_device_uuid = original

    conn = get_retail_conn()
    row = conn.execute("SELECT * FROM sales WHERE id=?", (sale['id'],)).fetchone()
    conn.close()
    assert row['terminal_id'] is None, (
        'a corrupt device identity produced a terminal_id anyway -- it was '
        'invented somewhere rather than read'
    )
    # Everything the device CAN still prove is still stamped.
    assert row['actor_user_uid'] == shop['user_uid']
    _assert_utc(row['created_at_utc'], 'sales.created_at_utc')
    _assert_uuid4(row['uid'], 'sales.uid')


def test_an_unresolvable_actor_leaves_the_column_null_rather_than_falling_back_to_the_local_id():
    """The fallback that must NOT exist.

    When the signed-in user's registry row has no `uid`, the honest answer is
    NULL: `users.id` is a private local detail, and writing it here would
    produce a value that every local check accepts and every peer resolves to
    nobody. The free-text `cashier` column still carries the local id, so no
    evidence is lost by refusing to guess.

    In production this state is transient at worst -- account_schema's
    `_backfill_uids` stamps any uid-less row on the next launch, and both
    account-creation routes write one inline.
    """
    shop = _make_shop()
    conn = registry_conn()
    conn.execute("UPDATE users SET uid=NULL WHERE id=?", (shop['user_id'],))
    conn.commit()
    conn.close()

    sale = _sell(shop)
    conn = get_retail_conn()
    row = conn.execute("SELECT * FROM sales WHERE id=?", (sale['id'],)).fetchone()
    conn.close()
    assert row['actor_user_uid'] is None, (
        f"actor_user_uid is {row['actor_user_uid']!r} for a user with no wire "
        f"identity -- something fell back to a local id"
    )
    assert row['cashier'] == shop['user_id'], 'the local free-text trail was lost too'
    assert row['terminal_id'] == TERMINAL_ID
    _assert_utc(row['created_at_utc'], 'sales.created_at_utc')
