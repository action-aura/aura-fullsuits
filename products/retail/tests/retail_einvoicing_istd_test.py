"""Aura Retail -- ISTD document profile wired through the real app
(JOFOTARA-1). Real sale -> real return -> the adapter's own outbox rows ->
build_document -> ISTD XML. Nothing here contacts ISTD: transport is still
NotImplemented and the outbox is never run against a real provider.

Rules asserted are listed in commercial_runtime/einvoicing/tests/
test_istd_profile.py (sources: docs/einvoicing/istd-profile.md).

Run (one file per process):
    python -m pytest products/retail/tests/retail_einvoicing_istd_test.py -v
"""
import os
import shutil
import sys
import tempfile
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_einv_istd_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)
os.environ["AURA_EINVOICING_ALLOW_MOCK"] = "1"

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402
seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")
os.environ.pop("AURA_RETAIL_DEMO_MODE", None)

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from commercial_runtime.einvoicing import settings as ei_settings  # noqa: E402
from commercial_runtime.einvoicing.document import document_uuid  # noqa: E402
from commercial_runtime.einvoicing.ubl import render_document_xml  # noqa: E402
from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402
from core.retail import einvoice_adapter as adapter  # noqa: E402
from database.schema import get_retail_conn  # noqa: E402

NS = {
    'cac': 'urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2',
    'cbc': 'urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2',
}


def teardown_module(module):
    for worker in _app_module._einvoicing_workers.values():
        worker.stop()
    shutil.rmtree(DATA, ignore_errors=True)


def _make_company(tax_rate=16.0, price=10.0):
    email = f"istd-{uuid.uuid4().hex[:10]}@test.local"
    company_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, require_password_change) "
        "VALUES (?,?,?,?,?,?,?,0)",
        (str(uuid.uuid4()), company_id, "EMP-0001", email, hash_password("IstdPW1"), "admin", "active"),
    )
    conn.commit()
    conn.close()
    rconn = get_retail_conn()
    rconn.execute("INSERT INTO branches (company_id,name) VALUES (?, 'Main')", (company_id,))
    branch_id = rconn.execute(
        "SELECT id FROM branches WHERE company_id=? ORDER BY id LIMIT 1", (company_id,)).fetchone()[0]
    pids = []
    for sku, rate in (('A-1', tax_rate), ('B-0', 0.0)):
        pid = str(uuid.uuid4())
        rconn.execute(
            "INSERT INTO products (id,company_id,sku,name,cost_price,sell_price,tax_rate) VALUES (?,?,?,?,1,?,?)",
            (pid, company_id, sku, 'Item ' + sku, price, rate))
        rconn.execute(
            "INSERT INTO inventory_balances (company_id,product_id,branch_id,quantity_on_hand) VALUES (?,?,?,50)",
            (company_id, pid, branch_id))
        pids.append(pid)
    rconn.execute("INSERT OR REPLACE INTO doc_sequences (company_id,doc_type,last_no) VALUES (?,'sale',777000)",
                  (company_id,))
    rconn.commit()
    rconn.close()
    client = app.test_client()
    client.post("/api/auth/login", json={"email": email, "password": "IstdPW1"})
    client.get("/api/sub/retail/settings/tax")
    return client, company_id, pids


def _configure(company_id, **kv):
    conn = get_retail_conn()
    for k, v in kv.items():
        ei_settings.set_setting(conn, company_id, k, v)
    conn.commit()
    conn.close()


def _opt_in(company_id):
    _configure(company_id, tax_regime='jordan', enabled='1', provider='direct_istd',
               invoice_family='general_sales', seller_tin='123456789', seller_name='Aura Test Shop', seller_activity_code='5390954')


def _sell(client, items):
    r = client.post('/api/sub/retail/sales', json={
        'items': [{'product_id': p, 'quantity': q} for p, q in items],
        'amount_paid': 999999, 'payment_method': 'cash', 'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']


def _count(sql, params):
    conn = get_retail_conn()
    try:
        return conn.execute(sql, params).fetchone()[0]
    finally:
        conn.close()


def _sequence_rows(company_id):
    return _count("SELECT COUNT(*) FROM einvoice_sequence WHERE company_id=?", (company_id,))


# ---- settings default (review D8), both directions ---------------------------

def test_unconfigured_shop_mints_no_numbers():
    client, cid, (pa, _pb) = _make_company()
    data = _sell(client, [(pa, 1)])
    assert 'einvoice' not in data
    assert _sequence_rows(cid) == 0                      # not one number minted
    assert _count("SELECT COUNT(*) FROM einvoice_outbox WHERE company_id=?", (cid,)) == 0


def test_opted_in_shop_does_mint():
    client, cid, (pa, _pb) = _make_company()
    _opt_in(cid)
    data = _sell(client, [(pa, 1)])
    assert data['einvoice']['status'] == 'queued'
    assert _sequence_rows(cid) == 1


# ---- invoice + credit note through the real adapter ---------------------------

def test_sale_then_partial_return_builds_invoice_and_referencing_credit_note():
    client, cid, (pa, pb) = _make_company()
    _opt_in(cid)
    sale = _sell(client, [(pa, 2), (pb, 4)])             # mixed 16% + 0%
    sale_ref = sale['einvoice']['invoice_ref']

    conn = get_retail_conn()
    srow = conn.execute("SELECT * FROM einvoice_outbox WHERE company_id=? AND invoice_ref=?", (cid, sale_ref)).fetchone()
    inv_root = ET.fromstring(render_document_xml(adapter.build_document(conn, srow)))
    conn.close()
    # R-ICV: the ICV is the dedicated sequence number, not the local number (SALE-7770xx).
    icv = inv_root.find('cac:AdditionalDocumentReference/cbc:UUID', NS).text
    assert icv == '1' and srow['einvoice_no'].endswith('000001')
    local_n = int(srow['local_document_no'].split('-')[1])          # 777001: the shop's own counter
    assert local_n > 777000 and int(icv) != local_n
    assert inv_root.find('cbc:UUID', NS).text == document_uuid(cid, sale_ref)
    cats = [c.find('cbc:ID', NS).text for c in inv_root.iterfind(
        'cac:InvoiceLine/cac:TaxTotal/cac:TaxSubtotal/cac:TaxCategory', NS)]
    assert cats == ['S', 'O']

    # Return 1 of the 2 taxed units. No create_return() edit: the worker's reconcile pass wires it.
    ret = client.post('/api/sub/retail/returns', json={
        'sale_id': sale['id'], 'items': [{'product_id': pa, 'quantity': 1}],
        'reason': 'defective', 'idempotency_key': str(uuid.uuid4())})
    assert ret.status_code == 200, ret.get_json()
    conn = get_retail_conn()
    adapter.reconcile_missing_sales(conn, cid, ei_settings.enabled_at(conn, cid))
    conn.commit()
    crow = conn.execute("SELECT * FROM einvoice_outbox WHERE company_id=? AND source_type='return'", (cid,)).fetchone()
    assert crow is not None
    cn_root = ET.fromstring(render_document_xml(adapter.build_document(conn, crow)))
    adapter.reconcile_missing_sales(conn, cid, ei_settings.enabled_at(conn, cid))   # second sweep: no second note
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM einvoice_outbox WHERE company_id=? AND source_type='return'",
                        (cid,)).fetchone()[0] == 1
    conn.close()

    tc = cn_root.find('cbc:InvoiceTypeCode', NS)
    assert (tc.text, tc.get('name')) == ('381', inv_root.find('cbc:InvoiceTypeCode', NS).get('name'))
    ref = cn_root.find('cac:BillingReference/cac:InvoiceDocumentReference', NS)
    assert ref.find('cbc:ID', NS).text == srow['einvoice_no']
    assert ref.find('cbc:UUID', NS).text == document_uuid(cid, sale_ref)
    assert ref.find('cbc:DocumentDescription', NS).text == \
        inv_root.find('cac:LegalMonetaryTotal/cbc:PayableAmount', NS).text
    assert cn_root.find('cac:AdditionalDocumentReference/cbc:UUID', NS).text == '2'      # ICV keeps climbing
    assert cn_root.find('cac:PaymentMeans/cbc:InstructionNote', NS).text == 'defective'
    assert cn_root.find('cac:LegalMonetaryTotal/cbc:PayableAmount', NS).text == '11.600'  # one 10.000 unit + 16%
    assert len(cn_root.findall('cac:InvoiceLine', NS)) == 1


def test_return_of_a_sale_that_was_never_einvoiced_gets_no_credit_note():
    client, cid, (pa, _pb) = _make_company()
    sale = _sell(client, [(pa, 1)])                      # shop not opted in: sale has no outbox row
    _opt_in(cid)
    ret = client.post('/api/sub/retail/returns', json={
        'sale_id': sale['id'], 'items': [{'product_id': pa, 'quantity': 1}],
        'reason': 'changed mind', 'idempotency_key': str(uuid.uuid4())})
    assert ret.status_code == 200, ret.get_json()
    return_id = _count("SELECT id FROM returns WHERE company_id=?", (cid,))
    assert adapter.enqueue_return(get_retail_conn, company_id=cid, return_id=return_id, return_number='X') is False
    conn = get_retail_conn()
    adapter.reconcile_missing_returns(conn, cid, '2000-01-01T00:00:00+00:00')
    conn.commit()
    conn.close()
    assert _count("SELECT COUNT(*) FROM einvoice_outbox WHERE company_id=?", (cid,)) == 0
    assert _sequence_rows(cid) == 0


def test_enqueue_return_is_invisible_when_not_opted_in():
    client, cid, (pa, _pb) = _make_company()
    assert adapter.enqueue_return(get_retail_conn, company_id=cid, return_id=1, return_number='R') is False
    assert _sequence_rows(cid) == 0
