"""Aura Retail -- final-review Fix 4 (2026-08-07): `delete_category` error
containment, exercised through the real HTTP route.

Before this fix, `delete_category` let a `sqlite3.IntegrityError` escape into
Flask's default handler -- a 500 with an HTML body -- and never closed its
connection on that path. The frontend's `_deleteCategory` then failed to
parse the HTML in `_del()`'s `.json()` and swallowed the throw in a bare
`catch(e){}`: the user clicked Delete and absolutely nothing happened.

Run:
    pytest products/retail/tests/retail_category_delete_route_test.py -v
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_catdel_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402

# Seeded BEFORE the app is built, matching retail_capability_guard_test.py --
# deleting a category is capability-guarded (`retail.product.create`), so an
# inactive license would 403 every test here before reaching the code paths
# this file is actually about.
seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True
# Never let Flask's test client re-raise the exception itself -- this file is
# specifically about what a real client sees over HTTP, and PROPAGATE_EXCEPTIONS
# would turn "Flask returned a 500 HTML page" into a raised exception instead.
app.config["PROPAGATE_EXCEPTIONS"] = False

from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402
from database.schema import get_retail_conn  # noqa: E402
from api import retail_api  # noqa: E402


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


@pytest.fixture(scope="module")
def client_and_company():
    email = f'catdel-admin-{uuid.uuid4().hex[:8]}@test.local'
    password = 'CatDelPW1'
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


def _make_category_with_product(company_id):
    cat_id = str(uuid.uuid4())
    conn = get_retail_conn()
    conn.execute("INSERT INTO categories (id, company_id, name) VALUES (?,?,?)", (cat_id, company_id, "Electronics"))
    sku = f"SKU-{uuid.uuid4().hex[:8]}"
    conn.execute(
        "INSERT INTO products (id, company_id, sku, name, category_id, sell_price) VALUES (?,?,?,?,?,?)",
        (str(uuid.uuid4()), company_id, sku, "Laptop", cat_id, 1199.99),
    )
    conn.commit()
    conn.close()
    return cat_id, sku


def _product_category(sku):
    conn = get_retail_conn()
    try:
        row = conn.execute("SELECT category_id FROM products WHERE sku=?", (sku,)).fetchone()
        return row["category_id"] if row else "<<missing>>"
    finally:
        conn.close()


def test_deleting_a_category_that_has_products_succeeds_and_unassigns_them(client_and_company):
    """The end-to-end shape of Fix 1 as the desktop user actually experiences
    it: Delete works, the product is kept, its category link is cleared."""
    c, cid = client_and_company
    cat_id, sku = _make_category_with_product(cid)

    r = c.delete(f'/api/sub/retail/categories/{cat_id}')

    assert r.status_code == 200, r.get_data(as_text=True)
    assert r.get_json()['status'] == 'success'
    assert _product_category(sku) is None


def test_deleting_a_missing_category_is_a_clean_404_json(client_and_company):
    c, _ = client_and_company
    r = c.delete(f'/api/sub/retail/categories/{uuid.uuid4()}')
    assert r.status_code == 404
    assert r.get_json()['status'] == 'error'


def test_an_integrity_error_becomes_a_clean_409_json_never_a_500_html_page(client_and_company, monkeypatch):
    """The containment itself. A future FK/constraint anywhere near this
    table must surface as JSON the UI can render, not as Flask's HTML 500 --
    which is exactly what `_del()`'s `.json()` choked on."""
    c, cid = client_and_company
    cat_id, sku = _make_category_with_product(cid)

    def _boom(*args, **kwargs):
        raise sqlite3.IntegrityError("FOREIGN KEY constraint failed")

    monkeypatch.setattr(retail_api, '_queue_sync_event', _boom)
    r = c.delete(f'/api/sub/retail/categories/{cat_id}')

    assert r.status_code == 409, r.get_data(as_text=True)
    assert r.is_json, "the UI parses this with .json(); an HTML error page is the bug"
    body = r.get_json()
    assert body['status'] == 'error'
    assert body['message']  # a real, showable message, not an empty string
    # Rolled back, not half-applied: the category must still be there.
    conn = get_retail_conn()
    try:
        assert conn.execute("SELECT COUNT(*) FROM categories WHERE id=?", (cat_id,)).fetchone()[0] == 1
    finally:
        conn.close()
    assert _product_category(sku) == cat_id


def test_a_non_integrity_database_error_becomes_a_clean_400_json(client_and_company, monkeypatch):
    c, cid = client_and_company
    cat_id, _ = _make_category_with_product(cid)

    def _boom(*args, **kwargs):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(retail_api, '_queue_sync_event', _boom)
    r = c.delete(f'/api/sub/retail/categories/{cat_id}')

    assert r.status_code == 400, r.get_data(as_text=True)
    assert r.is_json
    assert r.get_json()['status'] == 'error'


def test_the_route_still_works_after_a_failed_delete(client_and_company):
    """A failed delete must not leak the connection or leave the table
    locked -- the very next request has to behave normally."""
    c, cid = client_and_company
    cat_id, sku = _make_category_with_product(cid)
    r = c.delete(f'/api/sub/retail/categories/{cat_id}')
    assert r.status_code == 200, r.get_data(as_text=True)
    assert _product_category(sku) is None
    assert c.get('/api/sub/retail/categories').status_code == 200
