"""Aura Retail -- receipt identity PARITY between the checkout response
(create_sale) and a REPRINT (get_sale) of the same sale.

The point of this suite is PARITY, not presence. `_receiptIdentityBlock`
(products/retail/frontend/subsystem-retail.js) renders an optional
"Cashier:" row and an optional "Customer:" row on a printed receipt, gated
on the shop's own branding settings (branding_receipt_show_cashier /
branding_receipt_show_customer), and reads `saleData.employee_name`,
`saleData.customer_id`, `saleData.customer_name`. `get_sale` (retail_api.py
~line 6063) has always resolved all three for a REPRINT; `create_sale`
never did -- a shop that switched those branding settings on got the rows
on a reprint and silently never got them on the receipt actually handed
over at the till. This suite proves the two paths now AGREE on a real sale,
not merely that create_sale returns some non-null value (a test that only
checks presence would still pass if create_sale hardcoded an unrelated
name, or resolved a different customer than the one actually on the row).

Bootstrap pattern copied from retail_cash_drawer_test.py. Boots a real app
at import time -- run this file in its OWN pytest process, never alongside
another test file that also boots an app (AUDIT-010).

Run:
    pytest products/retail/tests/retail_receipt_identity_parity_test.py -v
"""
import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_receiptidentity_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402
seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


def _make_admin_and_product(price=100.0, tax_rate=0.0, stock=500):
    """New company + admin user, signed in, + one product -- same shape as
    retail_cash_drawer_test.py's own `_make_admin_and_product`. The admin is
    seeded with a REAL email (not left blank) so `_resolve_actor_identities`
    (email-first, per its own docstring) has something concrete to resolve
    `employee_name` to -- an anonymous/unresolvable actor would let a
    hardcoded-string mutation of the resolver call pass by accident.

    Returns (client, company_id, product_id, email) -- `email` is the
    expected `employee_name` value.
    """
    email = f"receiptid-{uuid.uuid4().hex[:10]}@test.local"
    password = "ReceiptIdPW1"  # pragma: allowlist secret -- throwaway test fixture password
    company_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    # `uid` (the registry's WIRE identity, distinct from the local `id`) must
    # be seeded explicitly -- `_actor_user_uid()` resolves the signed-in
    # user's `uid` column, never `id`, and deliberately returns None rather
    # than falling back when it's blank (see retail_reports_by_employee_
    # actor_lookup_test.py's "NULL-over-fallback policy" for the full
    # reasoning). Omit it here and `actor` in create_sale is None, both
    # `employee_name` resolutions come back None, and this whole parity
    # suite would pass for the wrong reason (both sides agreeing on
    # "unresolvable" rather than on a real identity).
    user_uid = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, uid, company_id, employee_id, email, password_hash, role, status, "
        "require_password_change) VALUES (?,?,?,?,?,?,?,?,0)",
        (user_id, user_uid, company_id, "EMP-9001", email, hash_password(password), "admin", "active"),
    )
    conn.commit()
    conn.close()

    client = app.test_client()
    client.post("/api/auth/login", json={"email": email, "password": password})

    resp = client.post('/api/sub/retail/products', json={
        'name': 'Receipt Identity Widget', 'sku': f'RI-{uuid.uuid4().hex[:8]}',
        'cost_price': price / 2, 'sell_price': price, 'tax_rate': tax_rate,
        'initial_stock': stock,
    })
    assert resp.status_code == 200, resp.get_json()
    product_id = resp.get_json()['data']['id']
    return client, company_id, product_id, email


def _make_customer(client, name):
    resp = client.post('/api/sub/retail/customers', json={'name': name})
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()['data']['id']


def _sell(client, product_id, customer_id=None, qty=1, unit_price=100.0):
    payload = {
        'items': [{'product_id': product_id, 'quantity': qty}],
        'amount_paid': unit_price * qty, 'payment_method': 'cash',
        'idempotency_key': str(uuid.uuid4()),
    }
    if customer_id is not None:
        payload['customer_id'] = customer_id
    return client.post('/api/sub/retail/sales', json=payload)


def _get_sale(client, sale_id):
    return client.get(f'/api/sub/retail/sales/{sale_id}')


# ── Named-customer sale: employee_name / customer_name / customer_id ───────

def test_checkout_employee_name_matches_reprint_and_is_not_none():
    """create_sale's `employee_name` must equal get_sale's for the SAME
    sale, and must not be None -- the explicit anti-vacuity check: without
    it, a create_sale that always returned None would still "match" a
    get_sale that also returned None whenever the actor happened to be
    unresolvable, and this test would pass for the wrong reason."""
    client, cid, pid, email = _make_admin_and_product()
    customer_id = _make_customer(client, 'Receipt Parity Customer')

    checkout = _sell(client, pid, customer_id=customer_id).get_json()['data']
    sale_id = checkout['id']
    reprint = _get_sale(client, sale_id).get_json()['data']['sale']

    assert checkout['employee_name'] is not None
    assert checkout['employee_name'] == reprint['employee_name']
    # Anchor to a KNOWN value, not just "the two sides agree on something" --
    # the admin was seeded with this exact email and _resolve_actor_
    # identities resolves email-first.
    assert checkout['employee_name'] == email


def test_checkout_customer_name_matches_reprint_and_the_customers_own_name():
    client, cid, pid, email = _make_admin_and_product()
    customer_id = _make_customer(client, 'Receipt Parity Customer')

    checkout = _sell(client, pid, customer_id=customer_id).get_json()['data']
    sale_id = checkout['id']
    reprint = _get_sale(client, sale_id).get_json()['data']['sale']

    assert checkout['customer_name'] == 'Receipt Parity Customer'
    assert checkout['customer_name'] == reprint['customer_name']


def test_checkout_customer_id_matches_reprint():
    client, cid, pid, email = _make_admin_and_product()
    customer_id = _make_customer(client, 'Receipt Parity Customer')

    checkout = _sell(client, pid, customer_id=customer_id).get_json()['data']
    sale_id = checkout['id']
    reprint = _get_sale(client, sale_id).get_json()['data']['sale']

    assert checkout['customer_id'] == customer_id
    assert checkout['customer_id'] == reprint['customer_id']


# ── Walk-in sale: the case most likely to drift (COALESCE vs None) ─────────

def test_walkin_sale_reports_walkin_on_both_paths_not_none():
    """No customer_id at all. get_sale's SELECT has always COALESCEd a NULL
    customer join to 'Walk-in'; create_sale's new lookup must reproduce
    that exact string, not None and not '' -- one path COALESCEs and a
    naive port of the other would just return None, which is exactly the
    drift this parity suite exists to catch."""
    client, cid, pid, email = _make_admin_and_product()

    checkout = _sell(client, pid, customer_id=None).get_json()['data']
    sale_id = checkout['id']
    reprint = _get_sale(client, sale_id).get_json()['data']['sale']

    assert checkout['customer_name'] == 'Walk-in'
    assert reprint['customer_name'] == 'Walk-in'
    assert not checkout['customer_id']
    assert not reprint['customer_id']


# ── Guard against accidentally dropping a pre-existing key ─────────────────

def test_response_still_contains_every_pre_existing_key():
    """The three new keys are additive. Guards against an edit that
    accidentally replaced response_data instead of extending it."""
    client, cid, pid, email = _make_admin_and_product()
    checkout = _sell(client, pid).get_json()['data']

    pre_existing = {
        'id', 'sale_number', 'idempotency_key', 'currency', 'subtotal',
        'discount_amount', 'tax_amount', 'change', 'total', 'amount_paid',
        'balance_due', 'warning', 'points_redeemed', 'points_redeemed_amount',
        'lines', 'calculation_version', 'oversold_past_recorded_stock',
    }
    assert pre_existing.issubset(checkout.keys())
    assert {'employee_name', 'customer_id', 'customer_name'}.issubset(checkout.keys())
