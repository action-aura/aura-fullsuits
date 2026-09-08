"""Aura Clinic -- a submitted e-invoice must declare the invoice_family its
OWN einvoice_no was allocated from, not whatever the live setting says at
submission time.

`_enqueue_on_conn` reads `invoice_family` ONCE and uses that single value
both to allocate the number (sequence.allocate_einvoice_number -- 'income'
is the INC- series, 'general_sales' is the GS- series) and to stamp
`einvoice_outbox.invoice_family`, committed together inside one
BEGIN IMMEDIATE. They are one atomic decision.

`build_document` then re-read the LIVE setting instead of using the row it
was handed. Retail's adapter does the opposite, with a nine-line comment
naming this exact hazard (products/retail/backend/core/retail/
einvoice_adapter.py). The clinic adapter's docstring enumerates "two real
differences from Retail" and this is not one of them, so it was drift, not
a decision.

What it produced: a clinic that changes invoice_family between enqueue and
worker submission files a document whose cbc:Note declares one family while
its own cbc:ID carries the other family's series prefix -- against the tax
authority, fingerprinted by document_sha256 as the compliance evidence of
what was filed. See docs/einvoicing/phase1/invoice-numbering-audit.md.

This is latent under the shipped UnconfiguredProvider (worker.run_once
returns early and never builds a document), so this file forces
AURA_EINVOICING_ALLOW_MOCK=1 and a real MockProvider round trip -- the same
way clinic_einvoicing_test.py does -- because the bug is only observable in
a document that actually got built.

Run:
    python -m pytest products/clinic/tests/clinic_einvoice_family_pinned_test.py -v
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

DATA = Path(tempfile.mkdtemp(prefix="aura_clinic_einv_family_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)
# Must be set before `import app`: the shipped default is UnconfiguredProvider,
# which never builds a document at all, and a document is what this file
# inspects. Development/test-only override; production never sets it.
os.environ["AURA_EINVOICING_ALLOW_MOCK"] = "1"

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402
seed_active_license(str(DATA), product_code="AURA_CLINIC", platform="WINDOWS")

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402
from database.schema import get_clinic_conn  # noqa: E402

_NS = {
    '': 'urn:oasis:names:specification:ubl:schema:xsd:Invoice-2',
    'cbc': 'urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2',
}


def teardown_module(module):
    for worker in _app_module._einvoicing_workers.values():
        worker.stop()
    shutil.rmtree(DATA, ignore_errors=True)


def _make_admin_and_patient():
    email = f"fam-{uuid.uuid4().hex[:10]}@test.local"
    password = "FamilyPW1"
    company_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, "
        "require_password_change) VALUES (?,?,?,?,?,?,?,0)",
        (str(uuid.uuid4()), company_id, "EMP-0001", email, hash_password(password), "admin", "active"),
    )
    conn.commit()
    conn.close()

    cconn = get_clinic_conn()
    cconn.execute("INSERT INTO clinic_patients (company_id,patient_code,name) VALUES (?,?,?)",
                  (company_id, f"PT-{uuid.uuid4().hex[:8]}", "Family Test Patient"))
    patient_id = cconn.execute(
        "SELECT id FROM clinic_patients WHERE company_id=? ORDER BY id DESC LIMIT 1", (company_id,)
    ).fetchone()[0]
    cconn.commit()
    cconn.close()

    client = app.test_client()
    assert client.post("/api/auth/login", json={"email": email, "password": password}).status_code == 200
    return client, company_id, patient_id


def _document_for(source_id):
    conn = get_clinic_conn()
    row = conn.execute(
        "SELECT einvoice_no, invoice_family, document_xml FROM einvoice_outbox "
        "WHERE source_type='clinic_invoice' AND source_id=?", (source_id,)).fetchone()
    conn.close()
    return row


def test_the_filed_document_declares_the_family_its_number_came_from():
    """Enqueue under 'income' (INC- series), flip the live setting to
    'general_sales', then submit. The document must still say 'income',
    because INC-nnnnnn came out of the income series and the two are one
    decision.

    A regression that re-reads the setting produces
    invoice_family=general_sales in the Note beside an INC- id -- a document
    that contradicts its own number.
    """
    client, cid, patient_id = _make_admin_and_patient()
    r = client.post('/api/einvoicing/settings',
                    json={'enabled': '1', 'provider': 'mock', 'invoice_family': 'income'})
    assert r.status_code == 200, r.get_json()

    inv = client.post('/api/sub/clinic/invoices', json={
        'patient_id': patient_id,
        'items': [{'description': 'Consultation', 'qty': 1, 'unit_price': 12.345}],
        'tax_rate': 0.16,
    }).get_json()['data']
    assert inv['einvoice']['status'] == 'queued'

    # The flip. Everything already enqueued keeps the family it was numbered
    # under; only later invoices belong to the new series.
    assert client.post('/api/einvoicing/settings',
                       json={'invoice_family': 'general_sales'}).status_code == 200

    assert client.post('/api/einvoicing/outbox/run-once').status_code == 200

    row = _document_for(inv['id'])
    assert row is not None and row['document_xml'], 'the worker must have built and stored a document'
    assert row['einvoice_no'].startswith('INC-'), row['einvoice_no']

    root = ET.fromstring(row['document_xml'])
    note = root.find('cbc:Note', _NS).text
    doc_id = root.find('cbc:ID', _NS).text

    assert 'invoice_family=income' in note, (
        f'the document must declare the family its number came from; got {note!r} '
        f'beside id {doc_id!r}')
    assert doc_id.startswith('INC-'), doc_id
    # The invariant, stated as the relationship rather than as two literals:
    # the declared family and the series prefix on the number must agree.
    assert ('income' in note) == doc_id.startswith('INC-')


def test_an_invoice_created_after_the_flip_uses_the_new_family():
    """The allow-half. Pinning the row's own family must not freeze the
    SETTING -- a clinic that switches to general_sales must actually start
    issuing GS- documents that declare general_sales. A 'fix' that hardcoded
    'income' passes the test above and breaks this one."""
    client, cid, patient_id = _make_admin_and_patient()
    assert client.post('/api/einvoicing/settings',
                       json={'enabled': '1', 'provider': 'mock',
                             'invoice_family': 'general_sales'}).status_code == 200

    inv = client.post('/api/sub/clinic/invoices', json={
        'patient_id': patient_id,
        'items': [{'description': 'Consultation', 'qty': 1, 'unit_price': 20.0}],
    }).get_json()['data']
    assert inv['einvoice']['status'] == 'queued'

    assert client.post('/api/einvoicing/outbox/run-once').status_code == 200

    row = _document_for(inv['id'])
    assert row['einvoice_no'].startswith('GS-'), row['einvoice_no']
    root = ET.fromstring(row['document_xml'])
    assert 'invoice_family=general_sales' in root.find('cbc:Note', _NS).text
    assert root.find('cbc:ID', _NS).text.startswith('GS-')


def test_the_filed_jod_document_carries_fils_and_reconciles():
    """End to end for the currency fix, through the real serializer rather
    than a hand-built document: a 12.345 JOD service at 16% VAT must reach
    the tax authority as 12.345 + 1.975 = 14.320, not as the old renderer's
    12.35 + 1.98 = 14.33 beside a PayableAmount of 14.32."""
    client, cid, patient_id = _make_admin_and_patient()
    assert client.post('/api/einvoicing/settings',
                       json={'enabled': '1', 'provider': 'mock', 'currency': 'JOD'}).status_code == 200

    inv = client.post('/api/sub/clinic/invoices', json={
        'patient_id': patient_id,
        'items': [{'description': 'Consultation', 'qty': 1, 'unit_price': 12.345}],
        'tax_rate': 0.16,
    }).get_json()['data']
    assert client.post('/api/einvoicing/outbox/run-once').status_code == 200

    root = ET.fromstring(_document_for(inv['id'])['document_xml'])
    ns = dict(_NS, cac='urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2')
    tax_excl = root.find('cac:LegalMonetaryTotal/cbc:TaxExclusiveAmount', ns).text
    tax = root.find('cac:TaxTotal/cbc:TaxAmount', ns).text
    payable = root.find('cac:LegalMonetaryTotal/cbc:PayableAmount', ns).text
    assert (tax_excl, tax, payable) == ('12.345', '1.975', '14.320')
    assert root.find('cbc:DocumentCurrencyCode', ns).text == 'JOD'
