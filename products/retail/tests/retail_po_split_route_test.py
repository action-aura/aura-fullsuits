"""
Aura Retail -- route-level coverage for the PO-preview-by-supplier foundation
(Thursday demo, Stream B): POST /purchase-orders/split-preview and the
supplier_contacts CRUD routes (GET/POST/PATCH/DELETE
/suppliers/<sid>/contacts[/<contact_id>]).

Complements (does not duplicate):
  - retail_po_split_migration_test.py -- schema v6 migration coverage.
  - retail_po_split_unit_test.py -- core/retail/po_split.py's pure grouping/
    contact-resolution/rounding logic, in isolation, no Flask/DB.

This file exercises the real Flask routes end to end (real app, real
sqlite-backed session, real DB) -- same bootstrap shape as
retail_capability_guard_test.py, since these routes are guarded by the same
@require_license_capability machinery and this file needs the identical
RESTRICTED-state toggling to prove the guard actually fires.

Run:
    pytest products/retail/tests/retail_po_split_route_test.py -v
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_po_split_route_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402
from commercial_runtime.licensing_contracts.state_repository import LicenseStateRepository  # noqa: E402

seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")
os.environ.pop("AURA_RETAIL_DEMO_MODE", None)

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402
from database.schema import get_retail_conn  # noqa: E402


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


def _db_path():
    return DATA / "database" / "subsystems" / "licensing.db"


def _set_state(state: str):
    repo = LicenseStateRepository(_db_path())
    record = repo.load()
    record.current_state = state
    repo.save(record)


def _make_admin_client():
    email = f'posplit-admin-{uuid.uuid4().hex[:8]}@test.local'
    password = 'PoSplitPW1'
    company_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, require_password_change) "
        "VALUES (?,?,?,?,?,?,?,0)",
        (user_id, company_id, 'EMP-0001', email, hash_password(password), 'admin', 'active'),
    )
    conn.commit()
    conn.close()
    c = app.test_client()
    r = c.post('/api/auth/login', json={'email': email, 'password': password})
    assert r.status_code == 200, r.get_json()
    return c, company_id


@pytest.fixture
def client_and_company():
    _set_state("ACTIVE_ONLINE")
    return _make_admin_client()


def _seed_supplier(c, name='Acme Supply', **kw):
    r = c.post('/api/sub/retail/suppliers', json={'name': name, **kw})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _set_min_order_value(sid, value):
    conn = get_retail_conn()
    conn.execute("UPDATE suppliers SET min_order_value=? WHERE id=?", (value, sid))
    conn.commit()
    conn.close()


def _seed_product(c, supplier_id=None, cost_price=10.0, sell_price=None, name='Split Test Product'):
    r = c.post('/api/sub/retail/products', json={
        'name': name, 'sku': f'SKU-{uuid.uuid4().hex[:8]}',
        'supplier_id': supplier_id, 'cost_price': cost_price,
        'sell_price': sell_price if sell_price is not None else cost_price * 2,
    })
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _create_contact(c, sid, **kw):
    payload = {'name': 'Orders Desk'}
    payload.update(kw)
    r = c.post(f'/api/sub/retail/suppliers/{sid}/contacts', json=payload)
    return r


# ═════════════════════════════════════════════════════════════════════════
# POST /purchase-orders/split-preview
# ═════════════════════════════════════════════════════════════════════════

def test_split_preview_groups_basket_by_supplier(client_and_company):
    c, _ = client_and_company
    s1 = _seed_supplier(c, name='Acme Supply')
    s2 = _seed_supplier(c, name='Beta Distribution')
    p1 = _seed_product(c, supplier_id=s1, cost_price=10.0)
    p2 = _seed_product(c, supplier_id=s2, cost_price=50.0)
    p3 = _seed_product(c, supplier_id=s1, cost_price=5.0)

    r = c.post('/api/sub/retail/purchase-orders/split-preview', json={'items': [
        {'product_id': p1, 'quantity': 2},
        {'product_id': p2, 'quantity': 1},
        {'product_id': p3, 'quantity': 3},
    ]})
    assert r.status_code == 200, r.get_json()
    data = r.get_json()['data']
    assert len(data['groups']) == 2
    by_supplier = {g['supplier_id']: g for g in data['groups']}
    assert by_supplier[s1]['line_count'] == 2
    assert by_supplier[s1]['subtotal'] == 35.0  # 2*10 + 3*5
    assert by_supplier[s2]['line_count'] == 1
    assert by_supplier[s2]['subtotal'] == 50.0
    assert data['unassigned']['line_count'] == 0


def test_split_preview_defaults_unit_cost_to_product_cost_price(client_and_company):
    c, _ = client_and_company
    s1 = _seed_supplier(c)
    p1 = _seed_product(c, supplier_id=s1, cost_price=17.5)

    r = c.post('/api/sub/retail/purchase-orders/split-preview', json={'items': [
        {'product_id': p1, 'quantity': 2},
    ]})
    assert r.status_code == 200, r.get_json()
    line = r.get_json()['data']['groups'][0]['lines'][0]
    assert line['unit_cost'] == 17.5
    assert line['line_total'] == 35.0


def test_split_preview_explicit_unit_cost_overrides_product_cost_price(client_and_company):
    c, _ = client_and_company
    s1 = _seed_supplier(c)
    p1 = _seed_product(c, supplier_id=s1, cost_price=17.5)

    r = c.post('/api/sub/retail/purchase-orders/split-preview', json={'items': [
        {'product_id': p1, 'quantity': 2, 'unit_cost': 9.0},
    ]})
    assert r.status_code == 200, r.get_json()
    line = r.get_json()['data']['groups'][0]['lines'][0]
    assert line['unit_cost'] == 9.0
    assert line['line_total'] == 18.0


def test_split_preview_unassigned_bucket_for_product_without_supplier(client_and_company):
    c, _ = client_and_company
    p1 = _seed_product(c, supplier_id=None, cost_price=8.0)

    r = c.post('/api/sub/retail/purchase-orders/split-preview', json={'items': [
        {'product_id': p1, 'quantity': 4},
    ]})
    assert r.status_code == 200, r.get_json()
    data = r.get_json()['data']
    assert data['groups'] == []
    assert data['unassigned']['line_count'] == 1
    assert data['unassigned']['subtotal'] == 32.0
    assert data['unassigned']['supplier_name'] == 'Unassigned'


def test_split_preview_moq_warning_boundary_exact_equals_not_below(client_and_company):
    c, _ = client_and_company
    s1 = _seed_supplier(c, name='At Minimum Supply')
    _set_min_order_value(s1, 20.0)
    p1 = _seed_product(c, supplier_id=s1, cost_price=10.0)

    r = c.post('/api/sub/retail/purchase-orders/split-preview', json={'items': [
        {'product_id': p1, 'quantity': 2},  # subtotal == 20.0, exactly the minimum
    ]})
    assert r.status_code == 200, r.get_json()
    group = r.get_json()['data']['groups'][0]
    assert group['subtotal'] == 20.0
    assert group['below_min_order'] is False


def test_split_preview_moq_warning_flags_group_strictly_below_minimum(client_and_company):
    c, _ = client_and_company
    s1 = _seed_supplier(c, name='Below Minimum Supply')
    _set_min_order_value(s1, 25.0)
    p1 = _seed_product(c, supplier_id=s1, cost_price=10.0)

    r = c.post('/api/sub/retail/purchase-orders/split-preview', json={'items': [
        {'product_id': p1, 'quantity': 2},  # subtotal == 20.0 < 25.0
    ]})
    assert r.status_code == 200, r.get_json()
    group = r.get_json()['data']['groups'][0]
    assert group['subtotal'] == 20.0
    assert group['below_min_order'] is True
    assert group['min_order_value'] == 25.0


def test_split_preview_resolves_primary_role_contact(client_and_company):
    c, _ = client_and_company
    s1 = _seed_supplier(c)
    p1 = _seed_product(c, supplier_id=s1, cost_price=10.0)
    cr = _create_contact(c, s1, name='Orders Desk', role='orders', is_primary=True,
                          email='orders@acme.test', channel_preference='email')
    assert cr.status_code == 200, cr.get_json()

    r = c.post('/api/sub/retail/purchase-orders/split-preview', json={'items': [
        {'product_id': p1, 'quantity': 1},
    ]})
    assert r.status_code == 200, r.get_json()
    contact = r.get_json()['data']['groups'][0]['contact']
    assert contact['source'] == 'contact_primary_role'
    assert contact['email'] == 'orders@acme.test'


def test_split_preview_requires_at_least_one_item(client_and_company):
    c, _ = client_and_company
    r = c.post('/api/sub/retail/purchase-orders/split-preview', json={'items': []})
    assert r.status_code == 400


def test_split_preview_rejects_unknown_product_id_by_name(client_and_company):
    c, _ = client_and_company
    bogus = str(uuid.uuid4())
    r = c.post('/api/sub/retail/purchase-orders/split-preview', json={'items': [
        {'product_id': bogus, 'quantity': 1},
    ]})
    assert r.status_code == 400
    assert bogus in r.get_json()['message']


def test_split_preview_rejects_invalid_quantity(client_and_company):
    c, _ = client_and_company
    s1 = _seed_supplier(c)
    p1 = _seed_product(c, supplier_id=s1)
    r = c.post('/api/sub/retail/purchase-orders/split-preview', json={'items': [
        {'product_id': p1, 'quantity': 0},
    ]})
    assert r.status_code == 400


def test_split_preview_rejects_cross_tenant_product_id(client_and_company):
    """The known-bad precedent is create_purchase_order, which never checks
    that product_id/supplier_id belong to the caller's company (a real,
    pre-existing, deliberately NOT-fixed-here bug). This route must not
    repeat that mistake: a product_id that is real but belongs to a
    DIFFERENT company must be rejected exactly like an unknown one --
    company A can prove nothing about company B's catalog from the error."""
    c_a, _cid_a = client_and_company
    c_b, _cid_b = _make_admin_client()
    s_b = _seed_supplier(c_b)
    p_b = _seed_product(c_b, supplier_id=s_b)

    r = c_a.post('/api/sub/retail/purchase-orders/split-preview', json={'items': [
        {'product_id': p_b, 'quantity': 1},
    ]})
    assert r.status_code == 400
    assert p_b in r.get_json()['message']


def test_split_preview_blocked_in_restricted_license_state(client_and_company):
    c, _ = client_and_company
    s1 = _seed_supplier(c)
    p1 = _seed_product(c, supplier_id=s1)
    _set_state("RESTRICTED")
    r = c.post('/api/sub/retail/purchase-orders/split-preview', json={'items': [
        {'product_id': p1, 'quantity': 1},
    ]})
    assert r.status_code == 403


def test_split_preview_requires_auth(client_and_company):
    with app.test_client() as anon:
        r = anon.post('/api/sub/retail/purchase-orders/split-preview', json={'items': []})
        assert r.status_code == 401


# ═════════════════════════════════════════════════════════════════════════
# Supplier contacts CRUD
# ═════════════════════════════════════════════════════════════════════════

def test_create_and_list_supplier_contact(client_and_company):
    c, _ = client_and_company
    s1 = _seed_supplier(c)
    cr = _create_contact(c, s1, name='Accounts Desk', role='accounts', email='ap@acme.test')
    assert cr.status_code == 200, cr.get_json()
    contact_id = cr.get_json()['data']['id']

    lr = c.get(f'/api/sub/retail/suppliers/{s1}/contacts')
    assert lr.status_code == 200, lr.get_json()
    rows = lr.get_json()['data']
    assert any(row['id'] == contact_id and row['name'] == 'Accounts Desk' for row in rows)


def test_create_contact_requires_name(client_and_company):
    c, _ = client_and_company
    s1 = _seed_supplier(c)
    r = c.post(f'/api/sub/retail/suppliers/{s1}/contacts', json={'role': 'orders'})
    assert r.status_code == 400


def test_create_contact_unknown_supplier_404(client_and_company):
    c, _ = client_and_company
    r = c.post(f'/api/sub/retail/suppliers/{uuid.uuid4()}/contacts', json={'name': 'Nobody'})
    assert r.status_code == 404


def test_create_contact_cross_tenant_supplier_rejected(client_and_company):
    c_a, _ = client_and_company
    c_b, _ = _make_admin_client()
    s_b = _seed_supplier(c_b)

    r = c_a.post(f'/api/sub/retail/suppliers/{s_b}/contacts', json={'name': 'Should Not Attach'})
    assert r.status_code == 404


def test_update_contact_fields(client_and_company):
    c, _ = client_and_company
    s1 = _seed_supplier(c)
    contact_id = _create_contact(c, s1, name='Orders Desk', phone='000').get_json()['data']['id']

    r = c.patch(f'/api/sub/retail/suppliers/{s1}/contacts/{contact_id}', json={'phone': '+1-555-9999'})
    assert r.status_code == 200, r.get_json()

    row = next(row for row in c.get(f'/api/sub/retail/suppliers/{s1}/contacts').get_json()['data']
               if row['id'] == contact_id)
    assert row['phone'] == '+1-555-9999'


def test_update_contact_cross_tenant_404(client_and_company):
    c_a, _ = client_and_company
    c_b, _ = _make_admin_client()
    s_b = _seed_supplier(c_b)
    contact_b = _create_contact(c_b, s_b).get_json()['data']['id']

    r = c_a.patch(f'/api/sub/retail/suppliers/{s_b}/contacts/{contact_b}', json={'name': 'Hijacked'})
    assert r.status_code == 404


def test_delete_contact_soft_deletes_and_row_survives(client_and_company):
    c, _ = client_and_company
    s1 = _seed_supplier(c)
    contact_id = _create_contact(c, s1, name='Retiring Contact').get_json()['data']['id']

    dr = c.delete(f'/api/sub/retail/suppliers/{s1}/contacts/{contact_id}')
    assert dr.status_code == 200, dr.get_json()

    row = next(row for row in c.get(f'/api/sub/retail/suppliers/{s1}/contacts').get_json()['data']
               if row['id'] == contact_id)
    assert row['status'] == 'inactive'  # soft-deleted, row still present


def test_delete_contact_unknown_404(client_and_company):
    c, _ = client_and_company
    s1 = _seed_supplier(c)
    r = c.delete(f'/api/sub/retail/suppliers/{s1}/contacts/{uuid.uuid4()}')
    assert r.status_code == 404


def test_primary_contact_invariant_on_create(client_and_company):
    c, _ = client_and_company
    s1 = _seed_supplier(c)
    first = _create_contact(c, s1, name='First Primary', role='orders', is_primary=True).get_json()['data']['id']
    second = _create_contact(c, s1, name='Second Primary', role='orders', is_primary=True).get_json()['data']['id']

    rows = {row['id']: row for row in c.get(f'/api/sub/retail/suppliers/{s1}/contacts').get_json()['data']}
    assert rows[first]['is_primary'] == 0
    assert rows[second]['is_primary'] == 1


def test_primary_contact_invariant_on_update(client_and_company):
    c, _ = client_and_company
    s1 = _seed_supplier(c)
    first = _create_contact(c, s1, name='Contact A', role='orders', is_primary=True).get_json()['data']['id']
    second = _create_contact(c, s1, name='Contact B', role='orders', is_primary=False).get_json()['data']['id']

    r = c.patch(f'/api/sub/retail/suppliers/{s1}/contacts/{second}', json={'is_primary': True})
    assert r.status_code == 200, r.get_json()

    rows = {row['id']: row for row in c.get(f'/api/sub/retail/suppliers/{s1}/contacts').get_json()['data']}
    assert rows[first]['is_primary'] == 0
    assert rows[second]['is_primary'] == 1


def test_primary_contact_invariant_scoped_per_role(client_and_company):
    """Two different roles on the same supplier must each be able to hold
    their own primary contact independently -- the invariant is (supplier,
    role)-scoped, not supplier-wide."""
    c, _ = client_and_company
    s1 = _seed_supplier(c)
    orders_primary = _create_contact(c, s1, name='Orders Primary', role='orders', is_primary=True).get_json()['data']['id']
    accounts_primary = _create_contact(c, s1, name='Accounts Primary', role='accounts', is_primary=True).get_json()['data']['id']

    rows = {row['id']: row for row in c.get(f'/api/sub/retail/suppliers/{s1}/contacts').get_json()['data']}
    assert rows[orders_primary]['is_primary'] == 1
    assert rows[accounts_primary]['is_primary'] == 1


def test_contacts_list_still_readable_when_restricted(client_and_company):
    c, _ = client_and_company
    s1 = _seed_supplier(c)
    _create_contact(c, s1, name='Reader Check')
    _set_state("RESTRICTED")
    r = c.get(f'/api/sub/retail/suppliers/{s1}/contacts')
    assert r.status_code == 200


def test_contacts_mutation_blocked_when_restricted(client_and_company):
    c, _ = client_and_company
    s1 = _seed_supplier(c)
    _set_state("RESTRICTED")
    r = c.post(f'/api/sub/retail/suppliers/{s1}/contacts', json={'name': 'Should Be Blocked'})
    assert r.status_code == 403


def test_contacts_require_auth(client_and_company):
    c, _cid = client_and_company
    s1 = _seed_supplier(c)
    with app.test_client() as anon:
        r = anon.get(f'/api/sub/retail/suppliers/{s1}/contacts')
        assert r.status_code == 401
