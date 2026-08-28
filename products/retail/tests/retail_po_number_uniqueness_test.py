"""Aura Retail -- `purchase_orders.po_number` uniqueness and error
containment (ROADMAP.md, 2026-08-28: "`po_number` collides across
companies, and the failure is not contained").

`purchase_orders.po_number` carries a bare (not per-company, not
per-device) `UNIQUE` constraint (database/schema.py), but
`_next_ref(conn, cid, 'po')` numbers it PER COMPANY, starting at 1. Two
different companies on one install therefore both minted "PO-000001" for
their first purchase order, and the second raised `sqlite3.IntegrityError`.
This is the identical bug class AUDIT-032B already fixed for
`sales.sale_number`/`returns.return_number` (see create_sale's and
create_return's own comments in api/retail_api.py, and
`_device_doc_discriminator`'s docstring) -- appending a per-company and a
per-device fragment to the sequential `_next_ref()` output. That fix was
never applied to `po_number` until now, and there are exactly TWO mint
sites in api/retail_api.py (`grep -n "_next_ref(conn, cid, 'po')"`):
`create_purchase_order` and `accept_reorder_request` (the reorder-accept
path, which drafts a PO). Both needed the identical treatment -- fixing
only one leaves the bug fully live through the other.

The second, independent half of the same ROADMAP entry: `create_purchase_
order` had NO `try/except` around its INSERT at all, so the resulting
`IntegrityError` escaped with the connection never closed -- it leaked,
holding a WAL write lock, and every LATER write on that database failed
with "database is locked". One colliding PO took the whole install down,
not just that one request. `accept_reorder_request` already had
`try/except/finally: conn.close()`; only `create_purchase_order` leaked.
`create_purchase_order` now gets the same containment shape
`delete_category` already uses (its own Fix 4 comment, this same file),
kept even though the fragment fix above makes the SPECIFIC collision this
file reproduces unreachable in practice -- it is what stops the NEXT
constraint added anywhere near this table from reintroducing the identical
install-wide outage.

This file follows the same self-contained bootstrap convention as
retail_reorder_sync_test.py / retail_category_delete_route_test.py (no
shared conftest.py exists for products/retail/tests/): its own temp
app-data dir, its own license seed, its own Flask app boot, its own
fixtures local to this file. `PROPAGATE_EXCEPTIONS = False` matches
retail_category_delete_route_test.py's own reasoning exactly -- this file
is specifically about what a real client sees over HTTP, and the default
TESTING-mode behavior would turn "Flask returned a 500 HTML page" into a
raised exception inside the test instead.

Run:
    pytest products/retail/tests/retail_po_number_uniqueness_test.py -v
"""
import os
import re
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_po_number_uniq_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402

# Seeded BEFORE the app is built -- creating a purchase order and accepting a
# reorder request are both capability-guarded, same convention as every
# other route-level test file in this suite.
seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True
# See module docstring -- this file asserts on the JSON shape of the HTTP
# response a real client sees, including the containment's error path.
app.config["PROPAGATE_EXCEPTIONS"] = False

from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402
from database.schema import get_retail_conn  # noqa: E402


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


def _make_admin_client(prefix):
    """A fresh, logged-in admin client for its OWN newly-created company --
    every test below needs at least one company that has never minted a PO
    before, so `doc_sequences` starts at 0 for it exactly like a brand-new
    install."""
    email = f'{prefix}-{uuid.uuid4().hex[:8]}@test.local'
    password = 'PoNumberPW1'
    company_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, require_password_change) "
        "VALUES (?,?,?,?,?,?,?,0)",
        (str(uuid.uuid4()), company_id, 'EMP-0001', email, hash_password(password), 'admin', 'active'),
    )
    conn.commit()
    conn.close()
    c = app.test_client()
    r = c.post('/api/auth/login', json={'email': email, 'password': password})
    assert r.status_code == 200, r.get_json()
    return c, company_id


@pytest.fixture
def two_companies():
    """Two independently-created companies on this ONE shared install/test
    database -- reproduces the exact multi-tenant shape the ROADMAP entry
    describes, rather than one company acting twice."""
    return _make_admin_client('ponum-a'), _make_admin_client('ponum-b')


@pytest.fixture
def client_and_company():
    return _make_admin_client('ponum-solo')


def _seed_product(c, **kw):
    payload = {
        'name': 'PO Number Test Widget', 'sku': f'PONUM-{uuid.uuid4().hex[:8]}',
        'cost_price': 5.0, 'sell_price': 10.0,
    }
    payload.update(kw)
    r = c.post('/api/sub/retail/products', json=payload)
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _create_po(c, product_id, **kw):
    payload = {'items': [{'product_id': product_id, 'quantity': 1, 'unit_cost': 5.0}]}
    payload.update(kw)
    return c.post('/api/sub/retail/purchase-orders', json=payload)


def _trigger_pending_reorder_request(c, *, reorder_level=5, initial_stock=6, sell_qty=2):
    """Drops a fresh product's stock to/below its reorder_level through a
    REAL sale, exactly like retail_reorder_sync_test.py's own `_make_product`
    / `_sell` pair -- this is the only way a `reorder_requests` row reaches
    'pending' outside of hand-crafting one, since there is no direct
    "create a reorder request" route (core/retail/reorder_hook.py's
    post-sale trigger is the sole writer)."""
    pid = _seed_product(
        c, name='Reorder Trigger Widget', reorder_level=reorder_level,
        reorder_method='whatsapp', initial_stock=initial_stock,
    )
    sale = c.post('/api/sub/retail/sales', json={
        'items': [{'product_id': pid, 'quantity': sell_qty}],
        'amount_paid': 999999, 'payment_method': 'cash', 'idempotency_key': str(uuid.uuid4()),
    })
    assert sale.status_code == 200, sale.get_json()
    conn = get_retail_conn()
    try:
        row = conn.execute(
            "SELECT id FROM reorder_requests WHERE product_id=? AND status='pending'", (pid,)
        ).fetchone()
    finally:
        conn.close()
    assert row is not None, "the post-sale reorder hook did not create a pending request"
    return row['id']


# ═════════════════════════════════════════════════════════════════════════
# 1. The collision itself -- create_purchase_order
# ═════════════════════════════════════════════════════════════════════════

def test_two_companies_on_one_install_can_each_create_a_first_purchase_order(two_companies):
    """The collision, reproduced at the route level. Both companies' `doc_
    sequences` counter for doc_type='po' starts at 0 -- before the
    company+device fragments, both minted "PO-000001" and the second
    company's INSERT raised `sqlite3.IntegrityError` against the bare
    UNIQUE constraint on `po_number`."""
    (client_a, cid_a), (client_b, cid_b) = two_companies
    pid_a = _seed_product(client_a)
    pid_b = _seed_product(client_b)

    po_a = _create_po(client_a, pid_a)
    assert po_a.status_code == 200, po_a.get_json()
    po_b = _create_po(client_b, pid_b)
    assert po_b.status_code == 200, po_b.get_json()

    number_a = po_a.get_json()['data']['po_number']
    number_b = po_b.get_json()['data']['po_number']
    assert number_a != number_b, "two different companies' first PO must not share a po_number"


# ═════════════════════════════════════════════════════════════════════════
# 2. The SECOND mint site -- accept_reorder_request
# ═════════════════════════════════════════════════════════════════════════

def test_a_po_drafted_by_accepting_a_reorder_request_also_gets_a_unique_number(two_companies):
    """The mint site a fix could easily miss: `accept_reorder_request`
    drafts a purchase_orders row from its OWN `_next_ref(conn, cid, 'po')`
    call, independent of create_purchase_order's. A fix applied only to the
    obvious route would leave this one fully live -- both companies'
    reorder-accept path starts its own `doc_sequences` counter at 0 too."""
    (client_a, cid_a), (client_b, cid_b) = two_companies
    req_a = _trigger_pending_reorder_request(client_a)
    req_b = _trigger_pending_reorder_request(client_b)

    accept_a = client_a.post(f'/api/sub/retail/reorder-requests/{req_a}/accept')
    assert accept_a.status_code == 200, accept_a.get_json()
    accept_b = client_b.post(f'/api/sub/retail/reorder-requests/{req_b}/accept')
    assert accept_b.status_code == 200, accept_b.get_json()

    number_a = accept_a.get_json()['data']['po_number']
    number_b = accept_b.get_json()['data']['po_number']
    assert number_a != number_b, "two different companies' reorder-drafted PO must not share a po_number"


# ═════════════════════════════════════════════════════════════════════════
# 3. The ALLOW half -- still human-readable and sequential
# ═════════════════════════════════════════════════════════════════════════

def test_a_purchase_order_number_is_still_human_readable_and_sequential(client_and_company):
    """The whole point of `_next_ref` is a readable, searchable reference
    (e.g. "PO-000001") that a shop can read off a printed PO and find again.
    A fix that made the number opaque (e.g. dropping the sequential part
    entirely and keeping only the disambiguating fragments) would pass
    tests 1 and 2 above while making the feature worse for the shop -- this
    is the test that catches that."""
    c, _cid = client_and_company
    pid = _seed_product(c)
    po = _create_po(c, pid)
    assert po.status_code == 200, po.get_json()
    number = po.get_json()['data']['po_number']
    assert re.match(r'^PO-\d{6}-', number), \
        f"po_number must still start with a sequential PO-NNNNNN part, got {number!r}"


# ═════════════════════════════════════════════════════════════════════════
# 4. The containment -- Half 2, the most valuable test in this file
# ═════════════════════════════════════════════════════════════════════════

def test_a_failed_purchase_order_insert_does_not_wedge_later_writes(client_and_company):
    """Forces a REAL `sqlite3.IntegrityError` out of create_purchase_order's
    own INSERT (not a monkeypatched stand-in) by predicting the exact
    po_number the SECOND call will mint -- the sequential part increments
    deterministically per (company, 'po'), and the company+device fragments
    are stable within this one process/company -- and pre-inserting a row
    that already holds it, then asserts on TWO things independently:

      1. the route itself returns a clean JSON error, not a 500 HTML page
         (same shape as retail_category_delete_route_test.py's own
         containment test), and
      2. a SUBSEQUENT, unrelated write on the SAME database still succeeds
         -- proving the connection from the failed INSERT was actually
         closed rather than leaked, holding the WAL write lock. A test that
         only asserted (1) would still pass while (2) silently wedged every
         later request on the install, exactly as the ROADMAP entry
         describes.
    """
    c, cid = client_and_company
    pid = _seed_product(c)

    first = _create_po(c, pid)
    assert first.status_code == 200, first.get_json()
    first_number = first.get_json()['data']['po_number']

    # "PO-000001-<cid8>-<device8>" -- split rather than re-derive the
    # fragments from private helpers, so this test stays bound to the
    # ROUTE's actual output, not to internal implementation.
    parts = first_number.split('-')
    assert len(parts) == 4, f"unexpected po_number shape: {first_number!r}"
    _prefix, seq_str, cid_frag, device_frag = parts
    predicted_next = f"PO-{int(seq_str) + 1:06d}-{cid_frag}-{device_frag}"

    # Plant the collision directly, bypassing the route -- this is what a
    # second device/process minting the SAME next sequence number for this
    # company would produce, and is the shape the pre-fix production bug
    # itself took across two companies.
    seed_conn = get_retail_conn()
    seed_conn.execute(
        "INSERT INTO purchase_orders (company_id,po_number,status,subtotal,total) VALUES (?,?,?,?,?)",
        (cid, predicted_next, 'pending', 0, 0),
    )
    seed_conn.commit()
    seed_conn.close()

    second = _create_po(c, pid)
    assert second.status_code == 409, second.get_data(as_text=True)
    assert second.is_json, "the UI parses this with .json(); an HTML error page is the bug"
    body = second.get_json()
    assert body['status'] == 'error'
    assert body['message']  # a real, showable message, not an empty string

    # The valuable assertion: the connection from the failed INSERT above
    # must have been closed, or this next ordinary write hangs/fails with
    # "database is locked". Deliberately an UNRELATED write (a new
    # product, not another PO) -- the manually-planted collision row above
    # is still sitting in purchase_orders and `_next_ref`'s counter was
    # rolled back with the failed INSERT (same transaction), so a THIRD PO
    # attempt would legitimately recompute the identical predicted_next and
    # collide again; that would prove nothing about the leak this test
    # exists to catch. A completely unrelated write is the clean signal.
    third = c.post('/api/sub/retail/products', json={
        'name': 'Post-Collision Product', 'sku': f'PONUM-POST-{uuid.uuid4().hex[:8]}',
        'cost_price': 1.0, 'sell_price': 2.0,
    })
    assert third.status_code == 200, \
        f"a later write was wedged by the failed INSERT above: {third.get_data(as_text=True)}"
