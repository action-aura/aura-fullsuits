"""
Aura Retail -- import/export parity suite (Phase 2B).

Closes the gap flagged in docs/migration/retail-extraction-report.md: the
Retail import/export server-side handlers (api/retail_api's frontend calls
into api/import_api.py) were not extracted in Phase 2, so the frontend's
Import buttons would have 404'd. products/retail/backend/api/import_api.py
now exists (Retail-scoped extraction of the source's universal import
engine) and is registered in app.py -- these tests exercise it end to end.

Export: no export endpoint or frontend export call exists anywhere in the
source Action Aura Enterprise implementation (confirmed by inspection --
see docs/migration/retail-parity-matrix.md "export" row). This is not a gap
introduced by extraction. `test_no_export_endpoint_exists_yet` documents
that fact as a regression marker for when export is eventually built,
rather than fabricating an export feature that was never in the source.

Run:
    pytest products/retail/tests/retail_import_export_test.py -v
"""
import csv
import io
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_importexport_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)
os.environ.pop("AURA_RETAIL_DEMO_MODE", None)

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from database.schema import get_retail_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


def _make_admin_client():
    email = f"import-{uuid.uuid4().hex[:10]}@test.local"
    password = "ImportTestPW1"
    company_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, require_password_change) "
        "VALUES (?,?,?,?,?,?,?,0)",
        (user_id, company_id, "EMP-0001", email, hash_password(password), "admin", "active"),
    )
    conn.commit()
    conn.close()
    client = app.test_client()
    client.post("/api/auth/login", json={"email": email, "password": password})
    return client, company_id


def _csv_bytes(rows, headers):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=headers)
    w.writeheader()
    for r in rows:
        w.writerow(r)
    return buf.getvalue().encode('utf-8')


# ═════════════════════════════════════════════════════════════════════════════
# 1. Import routes do not return 404
# ═════════════════════════════════════════════════════════════════════════════

def test_schemas_route_not_404():
    client, _cid = _make_admin_client()
    r = client.get("/api/import/schemas")
    assert r.status_code != 404
    assert r.status_code == 200
    assert 'retail' in r.get_json()['schemas']


def test_parse_route_not_404():
    client, _cid = _make_admin_client()
    body = _csv_bytes([{'Product Name': 'Widget', 'SKU': 'W-1', 'Selling Price': '9.99'}],
                       ['Product Name', 'SKU', 'Selling Price'])
    r = client.post("/api/import/parse", data={
        'system': 'retail', 'entity': 'products',
        'file': (io.BytesIO(body), 'products.csv'),
    }, content_type='multipart/form-data')
    assert r.status_code != 404
    assert r.status_code == 200


def test_detect_route_not_404():
    client, _cid = _make_admin_client()
    body = _csv_bytes([{'Product Name': 'Widget', 'SKU': 'W-1', 'Selling Price': '9.99'}],
                       ['Product Name', 'SKU', 'Selling Price'])
    r = client.post("/api/import/detect", data={
        'file': (io.BytesIO(body), 'products.csv'),
    }, content_type='multipart/form-data')
    assert r.status_code != 404


def test_clean_route_not_404():
    client, _cid = _make_admin_client()
    body = _csv_bytes([{'Product Name': 'Widget', 'SKU': 'W-1', 'Selling Price': '9.99'}],
                       ['Product Name', 'SKU', 'Selling Price'])
    r = client.post("/api/import/clean", data={
        'system': 'retail', 'entity': 'products',
        'mapping': '{"name":"Product Name","sku":"SKU","sell_price":"Selling Price"}',
        'file': (io.BytesIO(body), 'products.csv'),
    }, content_type='multipart/form-data')
    assert r.status_code != 404


def test_execute_route_not_404():
    client, _cid = _make_admin_client()
    body = _csv_bytes([{'Product Name': 'Widget', 'SKU': 'W-EXEC-1', 'Selling Price': '9.99'}],
                       ['Product Name', 'SKU', 'Selling Price'])
    r = client.post("/api/import/execute", data={
        'system': 'retail', 'entity': 'products',
        'mapping': '{"name":"Product Name","sku":"SKU","sell_price":"Selling Price"}',
        'file': (io.BytesIO(body), 'products.csv'),
    }, content_type='multipart/form-data')
    assert r.status_code != 404


def test_smart_execute_route_not_404():
    client, _cid = _make_admin_client()
    body = _csv_bytes([{'Category Name': 'Beverages'}], ['Category Name'])
    r = client.post("/api/import/smart-execute", data={
        'targets': '[{"system":"retail","entity":"categories","mapping":{"name":"Category Name"}}]',
        'file': (io.BytesIO(body), 'categories.csv'),
    }, content_type='multipart/form-data')
    assert r.status_code != 404


# ═════════════════════════════════════════════════════════════════════════════
# 2. Supported file formats are accepted
# ═════════════════════════════════════════════════════════════════════════════

def test_csv_products_import_end_to_end():
    client, cid = _make_admin_client()
    body = _csv_bytes([
        {'Product Name': 'CSV Widget', 'SKU': 'CSV-1', 'Selling Price': '19.99', 'Cost Price': '10'},
    ], ['Product Name', 'SKU', 'Selling Price', 'Cost Price'])
    r = client.post("/api/import/execute", data={
        'system': 'retail', 'entity': 'products',
        'mapping': '{"name":"Product Name","sku":"SKU","sell_price":"Selling Price","cost_price":"Cost Price"}',
        'file': (io.BytesIO(body), 'products.csv'),
    }, content_type='multipart/form-data')
    assert r.status_code == 200, r.get_json()
    data = r.get_json()
    assert data['success'] is True
    assert data['imported'] == 1

    rconn = get_retail_conn()
    row = rconn.execute("SELECT * FROM products WHERE company_id=? AND sku='CSV-1'", (cid,)).fetchone()
    rconn.close()
    assert row is not None
    assert row['sell_price'] == 19.99


def test_xlsx_products_import_end_to_end():
    openpyxl = pytest.importorskip("openpyxl")
    client, cid = _make_admin_client()
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(['Product Name', 'SKU', 'Selling Price'])
    ws.append(['XLSX Widget', 'XLSX-1', 29.99])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    r = client.post("/api/import/execute", data={
        'system': 'retail', 'entity': 'products',
        'mapping': '{"name":"Product Name","sku":"SKU","sell_price":"Selling Price"}',
        'file': (buf, 'products.xlsx'),
    }, content_type='multipart/form-data')
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['imported'] == 1

    rconn = get_retail_conn()
    row = rconn.execute("SELECT * FROM products WHERE company_id=? AND sku='XLSX-1'", (cid,)).fetchone()
    rconn.close()
    assert row is not None


# ═════════════════════════════════════════════════════════════════════════════
# 3. Invalid files are rejected safely
# ═════════════════════════════════════════════════════════════════════════════

def test_garbage_bytes_rejected_not_500():
    client, _cid = _make_admin_client()
    r = client.post("/api/import/parse", data={
        'system': 'retail', 'entity': 'products',
        'file': (io.BytesIO(b'\xff\xfe\x00\x01not a real file'), 'weird.xlsx'),
    }, content_type='multipart/form-data')
    assert r.status_code == 400
    assert r.get_json()['success'] is False


def test_no_file_uploaded_rejected():
    client, _cid = _make_admin_client()
    r = client.post("/api/import/parse", data={'system': 'retail', 'entity': 'products'},
                     content_type='multipart/form-data')
    assert r.status_code == 400
    assert 'file' in r.get_json()['error'].lower()


def test_unknown_entity_rejected():
    client, _cid = _make_admin_client()
    body = _csv_bytes([{'Name': 'X'}], ['Name'])
    r = client.post("/api/import/execute", data={
        'system': 'retail', 'entity': 'not_a_real_entity',
        'mapping': '{}',
        'file': (io.BytesIO(body), 'x.csv'),
    }, content_type='multipart/form-data')
    assert r.status_code == 400
    assert r.get_json()['success'] is False


# ═════════════════════════════════════════════════════════════════════════════
# 4. Import does not silently corrupt existing records
# ═════════════════════════════════════════════════════════════════════════════

def test_import_does_not_touch_unrelated_products():
    client, cid = _make_admin_client()
    rconn = get_retail_conn()
    rconn.execute("INSERT INTO products (company_id,sku,name,cost_price,sell_price) VALUES (?,'PRE-EXISTING','Untouched Product',1,2)", (cid,))
    rconn.commit(); rconn.close()

    body = _csv_bytes([{'Product Name': 'New Import', 'SKU': 'NEW-SKU', 'Selling Price': '5'}],
                       ['Product Name', 'SKU', 'Selling Price'])
    r = client.post("/api/import/execute", data={
        'system': 'retail', 'entity': 'products',
        'mapping': '{"name":"Product Name","sku":"SKU","sell_price":"Selling Price"}',
        'file': (io.BytesIO(body), 'products.csv'),
    }, content_type='multipart/form-data')
    assert r.status_code == 200, r.get_json()

    rconn = get_retail_conn()
    pre = rconn.execute("SELECT * FROM products WHERE company_id=? AND sku='PRE-EXISTING'", (cid,)).fetchone()
    rconn.close()
    assert pre is not None
    assert pre['name'] == 'Untouched Product'


def test_reimporting_same_sku_updates_not_duplicates():
    client, cid = _make_admin_client()
    body1 = _csv_bytes([{'Product Name': 'Original Name', 'SKU': 'DUP-SKU', 'Selling Price': '10'}],
                        ['Product Name', 'SKU', 'Selling Price'])
    client.post("/api/import/execute", data={
        'system': 'retail', 'entity': 'products',
        'mapping': '{"name":"Product Name","sku":"SKU","sell_price":"Selling Price"}',
        'file': (io.BytesIO(body1), 'p1.csv'),
    }, content_type='multipart/form-data')

    body2 = _csv_bytes([{'Product Name': 'Updated Name', 'SKU': 'DUP-SKU', 'Selling Price': '20'}],
                        ['Product Name', 'SKU', 'Selling Price'])
    r2 = client.post("/api/import/execute", data={
        'system': 'retail', 'entity': 'products',
        'mapping': '{"name":"Product Name","sku":"SKU","sell_price":"Selling Price"}',
        'file': (io.BytesIO(body2), 'p2.csv'),
    }, content_type='multipart/form-data')
    assert r2.status_code == 200
    assert r2.get_json()['updated'] == 1

    rconn = get_retail_conn()
    rows = rconn.execute("SELECT * FROM products WHERE company_id=? AND sku='DUP-SKU'", (cid,)).fetchall()
    rconn.close()
    assert len(rows) == 1  # not duplicated
    assert rows[0]['name'] == 'Updated Name'
    assert rows[0]['sell_price'] == 20.0


def test_within_file_duplicate_rows_deduped_not_double_imported():
    client, cid = _make_admin_client()
    body = _csv_bytes([
        {'Product Name': 'Dup Row', 'SKU': 'SAME-SKU', 'Selling Price': '5'},
        {'Product Name': 'Dup Row', 'SKU': 'SAME-SKU', 'Selling Price': '5'},
    ], ['Product Name', 'SKU', 'Selling Price'])
    r = client.post("/api/import/execute", data={
        'system': 'retail', 'entity': 'products',
        'mapping': '{"name":"Product Name","sku":"SKU","sell_price":"Selling Price"}',
        'file': (io.BytesIO(body), 'dup.csv'),
    }, content_type='multipart/form-data')
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['imported'] == 1
    assert r.get_json()['cleaning']['categories']['duplicate'] == 1


# ═════════════════════════════════════════════════════════════════════════════
# 5. Export -- documented, not fabricated (see module docstring)
# ═════════════════════════════════════════════════════════════════════════════

def test_no_export_endpoint_exists_yet():
    """No export route exists in the source implementation (confirmed by
    inspection of api/import_api.py and static/js/subsystem-retail.js — no
    `export` route, no export fetch call anywhere). This test documents the
    current state as a marker: if an export endpoint is added later without
    updating this test, the assertion below will start failing and should
    be replaced with a real positive export test at that time."""
    client, _cid = _make_admin_client()
    r = client.get("/api/sub/retail/export")
    assert r.status_code == 404
    r2 = client.get("/api/import/export")
    assert r2.status_code == 404


# ═════════════════════════════════════════════════════════════════════════════
# 6. Authentication and Retail permissions are enforced
# ═════════════════════════════════════════════════════════════════════════════

def test_parse_requires_authentication():
    with app.test_client() as c:
        body = _csv_bytes([{'Name': 'X'}], ['Name'])
        r = c.post("/api/import/parse", data={
            'system': 'retail', 'entity': 'products',
            'file': (io.BytesIO(body), 'x.csv'),
        }, content_type='multipart/form-data')
        assert r.status_code == 401


def test_execute_requires_authentication():
    with app.test_client() as c:
        body = _csv_bytes([{'Name': 'X'}], ['Name'])
        r = c.post("/api/import/execute", data={
            'system': 'retail', 'entity': 'products', 'mapping': '{}',
            'file': (io.BytesIO(body), 'x.csv'),
        }, content_type='multipart/form-data')
        assert r.status_code == 401


def test_schemas_requires_authentication():
    with app.test_client() as c:
        r = c.get("/api/import/schemas")
        assert r.status_code == 401


# ═════════════════════════════════════════════════════════════════════════════
# 7. "Importing as" behavior preserved (explicit system/entity routing)
# ═════════════════════════════════════════════════════════════════════════════

def test_importing_as_categories_routes_to_categories_not_products():
    client, cid = _make_admin_client()
    body = _csv_bytes([{'Category Name': 'Snacks', 'Description': 'Chips and such'}],
                       ['Category Name', 'Description'])
    r = client.post("/api/import/execute", data={
        'system': 'retail', 'entity': 'categories',
        'mapping': '{"name":"Category Name","description":"Description"}',
        'file': (io.BytesIO(body), 'cats.csv'),
    }, content_type='multipart/form-data')
    assert r.status_code == 200, r.get_json()

    rconn = get_retail_conn()
    cat = rconn.execute("SELECT * FROM categories WHERE company_id=? AND name='Snacks'", (cid,)).fetchone()
    prod = rconn.execute("SELECT COUNT(*) FROM products WHERE company_id=? AND name='Snacks'", (cid,)).fetchone()[0]
    rconn.close()
    assert cat is not None
    assert prod == 0  # explicitly chosen entity is respected, not auto-routed elsewhere


def test_importing_as_suppliers_routes_to_suppliers():
    client, cid = _make_admin_client()
    body = _csv_bytes([{'Company Name': 'Acme Supply Co', 'Phone Number': '+1-555-9999'}],
                       ['Company Name', 'Phone Number'])
    r = client.post("/api/import/execute", data={
        'system': 'retail', 'entity': 'suppliers',
        'mapping': '{"name":"Company Name","phone":"Phone Number"}',
        'file': (io.BytesIO(body), 'suppliers.csv'),
    }, content_type='multipart/form-data')
    assert r.status_code == 200, r.get_json()

    rconn = get_retail_conn()
    sup = rconn.execute("SELECT * FROM suppliers WHERE company_id=? AND name='Acme Supply Co'", (cid,)).fetchone()
    rconn.close()
    assert sup is not None
    assert sup['phone'] == '+1-555-9999'


def test_entity_auto_detection_within_retail_schemas():
    client, _cid = _make_admin_client()
    body = _csv_bytes([{'SKU': 'AUTO-1', 'Product Name': 'Auto Detected', 'Selling Price': '15'}],
                       ['SKU', 'Product Name', 'Selling Price'])
    r = client.post("/api/import/detect", data={
        'file': (io.BytesIO(body), 'products.csv'),
    }, content_type='multipart/form-data')
    assert r.status_code == 200
    detected = r.get_json()['detected']
    assert any(d['system'] == 'retail' and d['entity'] == 'products' for d in detected)


# ═════════════════════════════════════════════════════════════════════════════
# 8. Empty and malformed files handled clearly
# ═════════════════════════════════════════════════════════════════════════════

def test_empty_csv_rejected_with_clear_message():
    client, _cid = _make_admin_client()
    r = client.post("/api/import/parse", data={
        'system': 'retail', 'entity': 'products',
        'file': (io.BytesIO(b''), 'empty.csv'),
    }, content_type='multipart/form-data')
    assert r.status_code == 400
    assert r.get_json()['success'] is False
    assert r.get_json()['error']


def test_header_only_csv_rejected_no_data_rows():
    client, _cid = _make_admin_client()
    r = client.post("/api/import/parse", data={
        'system': 'retail', 'entity': 'products',
        'file': (io.BytesIO(b'Product Name,SKU,Selling Price\n'), 'headeronly.csv'),
    }, content_type='multipart/form-data')
    assert r.status_code == 400
    assert 'no data rows' in r.get_json()['error'].lower()


def test_malformed_json_import_rejected_safely():
    client, _cid = _make_admin_client()
    r = client.post("/api/import/parse", data={
        'system': 'retail', 'entity': 'products',
        'file': (io.BytesIO(b'{not valid json'), 'broken.json'),
    }, content_type='multipart/form-data')
    assert r.status_code == 400
    assert r.get_json()['success'] is False


def test_rows_missing_required_field_are_skipped_not_crashed():
    client, cid = _make_admin_client()
    body = _csv_bytes([
        {'Product Name': '', 'SKU': 'MISSING-NAME', 'Selling Price': '10'},   # missing required name
        {'Product Name': 'Valid Row', 'SKU': 'VALID-1', 'Selling Price': '10'},
    ], ['Product Name', 'SKU', 'Selling Price'])
    r = client.post("/api/import/execute", data={
        'system': 'retail', 'entity': 'products',
        'mapping': '{"name":"Product Name","sku":"SKU","sell_price":"Selling Price"}',
        'file': (io.BytesIO(body), 'mixed.csv'),
    }, content_type='multipart/form-data')
    assert r.status_code == 200, r.get_json()
    data = r.get_json()
    assert data['imported'] == 1
    assert data['status'] == 'partial'
    assert data['cleaning']['categories']['missing'] == 1
