"""Aura Clinic -- JoFotara e-invoicing (docs/einvoicing/phase1/) integration
test. Mirrors products/retail/tests/retail_einvoicing_test.py -- see that
file's module docstring for the full rationale.

Run:
    python -m pytest products/clinic/tests/clinic_einvoicing_test.py -v
"""
import os
import shutil
import sys
import tempfile
import time
import uuid
from pathlib import Path

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA = Path(tempfile.mkdtemp(prefix="aura_clinic_einvoicing_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)
# AUDIT: see products/retail/tests/retail_einvoicing_test.py's identical
# comment -- app.py's shipped default provider is now UnconfiguredProvider,
# which deliberately refuses to submit anything, and this file's whole
# point is a REAL round trip through a provider
# (test_run_once_clears_and_qr_becomes_available,
# test_reconciliation_sweep_picks_up_a_lost_enqueue). Must be set before
# `import app` below. AURA_EINVOICING_ALLOW_MOCK=1 is a development/
# test-only override; production never sets it.
os.environ["AURA_EINVOICING_ALLOW_MOCK"] = "1"

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402
seed_active_license(str(DATA), product_code="AURA_CLINIC", platform="WINDOWS")

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from database.schema import get_clinic_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402


def teardown_module(module):
    for worker in _app_module._einvoicing_workers.values():
        worker.stop()
    shutil.rmtree(DATA, ignore_errors=True)


def _make_admin_and_patient():
    email = f"einv-{uuid.uuid4().hex[:10]}@test.local"
    password = "EinvPW1"
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

    cconn = get_clinic_conn()
    cconn.execute(
        "INSERT INTO clinic_patients (company_id,patient_code,name) VALUES (?,?,?)",
        (company_id, f"PT-{uuid.uuid4().hex[:8]}", "E-invoice Patient"),
    )
    patient_id = cconn.execute(
        "SELECT id FROM clinic_patients WHERE company_id=? ORDER BY id DESC LIMIT 1", (company_id,)
    ).fetchone()[0]
    cconn.commit()
    cconn.close()

    client = app.test_client()
    client.post("/api/auth/login", json={"email": email, "password": password})
    return client, company_id, patient_id


def _create_invoice(client, patient_id, unit_price=100.0, qty=1, tax_rate=0.0):
    r = client.post('/api/sub/clinic/invoices', json={
        'patient_id': patient_id,
        'items': [{'description': 'E-invoice Service', 'qty': qty, 'unit_price': unit_price}],
        'tax_rate': tax_rate,
    })
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']


def test_disabled_invoice_response_has_no_einvoice_key():
    # AUDIT: see retail_einvoicing_test.py's identical comment -- e-invoicing
    # now defaults ON, so a genuinely disabled company needs an explicit
    # write now. Fixture correction, not a weakened test.
    client, cid, patient_id = _make_admin_and_patient()
    client.post('/api/einvoicing/settings', json={'enabled': '0'})
    data = _create_invoice(client, patient_id)
    assert 'einvoice' not in data


def test_enable_then_invoice_enqueues_with_gapless_number():
    client, cid, patient_id = _make_admin_and_patient()
    r = client.post('/api/einvoicing/settings', json={'enabled': '1', 'provider': 'mock'})
    assert r.status_code == 200, r.get_json()

    inv1 = _create_invoice(client, patient_id)
    assert inv1['einvoice']['status'] == 'queued'
    inv2 = _create_invoice(client, patient_id)
    assert inv2['einvoice']['status'] == 'queued'

    outbox = client.get('/api/einvoicing/outbox').get_json()['data']
    numbers = sorted(row['einvoice_no'] for row in outbox)
    nums_int = sorted(int(n.split('-')[1]) for n in numbers)
    assert nums_int == list(range(nums_int[0], nums_int[0] + len(nums_int)))


def test_run_once_clears_and_qr_becomes_available():
    client, cid, patient_id = _make_admin_and_patient()
    client.post('/api/einvoicing/settings', json={'enabled': '1'})
    inv = _create_invoice(client, patient_id)
    ref = inv['einvoice']['invoice_ref']

    r = client.post('/api/einvoicing/outbox/run-once')
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['data']['outcomes']['cleared'] >= 1

    entry = client.get(f'/api/einvoicing/outbox/{ref}').get_json()['data']['entry']
    assert entry['status'] == 'CLEARED'
    assert entry['provider_uuid']

    qr = client.get(f'/api/einvoicing/qr/{ref}.svg')
    assert qr.status_code == 200
    assert qr.mimetype == 'image/svg+xml'


def test_slow_provider_does_not_slow_the_invoice_response():
    client, cid, patient_id = _make_admin_and_patient()
    client.post('/api/einvoicing/settings', json={'enabled': '1'})

    start = time.monotonic()
    _create_invoice(client, patient_id)
    elapsed = time.monotonic() - start
    assert elapsed < 2.0, "invoice creation must never wait on e-invoice submission"


def test_enqueue_exception_does_not_fail_the_invoice(monkeypatch):
    client, cid, patient_id = _make_admin_and_patient()
    client.post('/api/einvoicing/settings', json={'enabled': '1'})

    import core.clinic.einvoice_adapter as adapter

    def _boom(conn_factory, **kwargs):
        raise RuntimeError("simulated enqueue failure")

    monkeypatch.setattr(adapter, 'enqueue_invoice', _boom)
    data = _create_invoice(client, patient_id)
    assert 'id' in data
    assert 'einvoice' not in data


def test_reconciliation_sweep_picks_up_a_lost_enqueue():
    client, cid, patient_id = _make_admin_and_patient()
    client.post('/api/einvoicing/settings', json={'enabled': '1'})

    import core.clinic.einvoice_adapter as adapter
    original = adapter.enqueue_invoice

    def _fail_once(conn_factory, **kwargs):
        raise RuntimeError("simulated transient failure")

    adapter.enqueue_invoice = _fail_once
    try:
        inv = _create_invoice(client, patient_id)
    finally:
        adapter.enqueue_invoice = original
    assert 'einvoice' not in inv

    r = client.post('/api/einvoicing/outbox/run-once')
    assert r.status_code == 200

    outbox = client.get('/api/einvoicing/outbox').get_json()['data']
    matching = [row for row in outbox if row['source_id'] == inv['id']]
    assert len(matching) == 1, "reconciliation sweep must have enqueued the previously-lost invoice"


def test_enabled_at_prevents_backfilling_pre_enablement_invoices():
    # AUDIT: see retail_einvoicing_test.py's identical test for why this
    # explicit disable is now required (e-invoicing defaults ON).
    client, cid, patient_id = _make_admin_and_patient()
    client.post('/api/einvoicing/settings', json={'enabled': '0'})
    pre_inv = _create_invoice(client, patient_id)
    assert 'einvoice' not in pre_inv

    time.sleep(1.1)  # see retail_einvoicing_test.py's identical test for why

    client.post('/api/einvoicing/settings', json={'enabled': '1'})
    client.post('/api/einvoicing/outbox/run-once')

    outbox = client.get('/api/einvoicing/outbox').get_json()['data']
    matching = [row for row in outbox if row['source_id'] == pre_inv['id']]
    assert matching == [], "an invoice from before the feature was ever enabled must never be retroactively submitted"
