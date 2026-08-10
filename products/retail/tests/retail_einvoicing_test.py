"""Aura Retail -- JoFotara e-invoicing (docs/einvoicing/phase1/) integration
test. Exercises the real app -- real blueprint, real worker registry, real
adapter -- end to end against MockProvider (Step 13 of this wave's
implementation plan).

Bootstrap pattern copied from
products/retail/tests/retail_einvoicing_regression_test.py /
wave1c_financial_gate_test.py.

Run:
    python -m pytest products/retail/tests/retail_einvoicing_test.py -v
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_einvoicing_"))
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
from database.schema import get_retail_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402


def teardown_module(module):
    for worker in _app_module._einvoicing_workers.values():
        worker.stop()
    shutil.rmtree(DATA, ignore_errors=True)


def _make_admin_and_product(price=100.0, tax_rate=10.0, stock=50):
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

    rconn = get_retail_conn()
    rconn.execute("INSERT INTO branches (company_id,name) VALUES (?, 'Main')", (company_id,))
    branch_id = rconn.execute(
        "SELECT id FROM branches WHERE company_id=? ORDER BY id LIMIT 1", (company_id,)
    ).fetchone()[0]
    # products.id is a client-generated TEXT UUID (multi-device sync
    # foundation), not an autoincrement integer -- unlike the old schema this
    # file was originally written against, a raw INSERT that omits id gets a
    # NULL primary key, not an auto-assigned one.
    product_id = str(uuid.uuid4())
    rconn.execute(
        "INSERT INTO products (id,company_id,sku,name,cost_price,sell_price,tax_rate) VALUES (?, ?, 'EI-1','E-invoice Item',5,?,?)",
        (product_id, company_id, price, tax_rate),
    )
    rconn.execute(
        "INSERT INTO inventory_balances (company_id,product_id,branch_id,quantity_on_hand) VALUES (?,?,?,?)",
        (company_id, product_id, branch_id, stock),
    )
    rconn.commit()
    rconn.close()

    client = app.test_client()
    client.post("/api/auth/login", json={"email": email, "password": password})
    client.get("/api/sub/retail/settings/tax")

    import random as _random
    dconn = get_retail_conn()
    dconn.execute(
        "INSERT OR REPLACE INTO doc_sequences (company_id,doc_type,last_no) VALUES (?,'sale',?)",
        (company_id, _random.randint(1, 5_000_000)),
    )
    dconn.commit()
    dconn.close()

    return client, company_id, product_id, branch_id


def _sell(client, pid, quantity=1, extra=None):
    payload = {
        'items': [{'product_id': pid, 'quantity': quantity}],
        'amount_paid': 999999, 'payment_method': 'cash', 'idempotency_key': str(uuid.uuid4()),
    }
    if extra:
        payload.update(extra)
    r = client.post('/api/sub/retail/sales', json=payload)
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']


def test_disabled_sale_response_has_no_einvoice_key():
    client, cid, pid, bid = _make_admin_and_product()
    data = _sell(client, pid)
    assert 'einvoice' not in data


def test_enable_then_sale_enqueues_with_gapless_number():
    client, cid, pid, bid = _make_admin_and_product()
    r = client.post('/api/einvoicing/settings', json={'enabled': '1', 'provider': 'mock'})
    assert r.status_code == 200, r.get_json()

    sale1 = _sell(client, pid)
    assert sale1['einvoice']['status'] == 'queued'
    sale2 = _sell(client, pid)
    assert sale2['einvoice']['status'] == 'queued'

    # /api/einvoicing/outbox is already scoped to the requesting session's
    # company by routes.py -- every row returned belongs to this company.
    outbox = client.get('/api/einvoicing/outbox').get_json()['data']
    numbers = sorted(row['einvoice_no'] for row in outbox)
    # gapless within this company: the two numbers this sequence issued are consecutive
    nums_int = sorted(int(n.split('-')[1]) for n in numbers)
    assert nums_int == list(range(nums_int[0], nums_int[0] + len(nums_int)))


def test_run_once_clears_and_qr_becomes_available():
    client, cid, pid, bid = _make_admin_and_product()
    client.post('/api/einvoicing/settings', json={'enabled': '1'})
    sale = _sell(client, pid)
    ref = sale['einvoice']['invoice_ref']

    r = client.post('/api/einvoicing/outbox/run-once')
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['data']['outcomes']['cleared'] >= 1

    entry = client.get(f'/api/einvoicing/outbox/{ref}').get_json()['data']['entry']
    assert entry['status'] == 'CLEARED'
    assert entry['provider_uuid']

    qr = client.get(f'/api/einvoicing/qr/{ref}.svg')
    assert qr.status_code == 200
    assert qr.mimetype == 'image/svg+xml'


def test_slow_provider_does_not_slow_the_sale_response():
    """The sale itself must return instantly regardless of how slow the
    e-invoice submission would be -- submission is async, via the worker,
    never inline with checkout."""
    client, cid, pid, bid = _make_admin_and_product()
    client.post('/api/einvoicing/settings', json={'enabled': '1'})

    start = time.monotonic()
    _sell(client, pid)
    elapsed = time.monotonic() - start
    assert elapsed < 2.0, "checkout must never wait on e-invoice submission"


def test_enqueue_exception_does_not_fail_the_sale(monkeypatch):
    client, cid, pid, bid = _make_admin_and_product()
    client.post('/api/einvoicing/settings', json={'enabled': '1'})

    import core.retail.einvoice_adapter as adapter

    def _boom(conn_factory, **kwargs):
        raise RuntimeError("simulated enqueue failure")

    monkeypatch.setattr(adapter, 'enqueue_sale', _boom)
    # retail_api.py imports enqueue_sale by name at call time (inside the
    # try block), so patching the adapter module's attribute is sufficient.
    data = _sell(client, pid)
    assert 'id' in data  # the sale itself still succeeded
    assert 'einvoice' not in data


def test_reconciliation_sweep_picks_up_a_lost_enqueue():
    """Simulates the enqueue hook having failed silently (as it's designed
    to) -- the sale exists but no outbox row does. The worker's
    reconciliation sweep (via run-once) must find and enqueue it."""
    client, cid, pid, bid = _make_admin_and_product()
    client.post('/api/einvoicing/settings', json={'enabled': '1'})

    import core.retail.einvoice_adapter as adapter
    original = adapter.enqueue_sale
    calls = {'n': 0}

    def _fail_once(conn_factory, **kwargs):
        calls['n'] += 1
        raise RuntimeError("simulated transient failure")

    adapter.enqueue_sale = _fail_once
    try:
        sale = _sell(client, pid)
    finally:
        adapter.enqueue_sale = original
    assert 'einvoice' not in sale

    r = client.post('/api/einvoicing/outbox/run-once')
    assert r.status_code == 200

    outbox = client.get('/api/einvoicing/outbox').get_json()['data']
    matching = [row for row in outbox if row['source_id'] == sale['id']]
    assert len(matching) == 1, "reconciliation sweep must have enqueued the previously-lost sale"


def test_enabled_at_prevents_backfilling_pre_enablement_sales():
    client, cid, pid, bid = _make_admin_and_product()
    # Sale happens BEFORE the feature is ever enabled.
    pre_sale = _sell(client, pid)
    assert 'einvoice' not in pre_sale

    # created_at / enabled_at only have 1-second string precision (see
    # einvoice_adapter.reconcile_missing_sales's docstring) -- without a
    # real gap, the pre-sale and the enable call could truncate to the
    # identical second and this test would be asserting an artificial
    # tie, not the real "before enablement" guarantee.
    time.sleep(1.1)

    client.post('/api/einvoicing/settings', json={'enabled': '1'})
    client.post('/api/einvoicing/outbox/run-once')  # runs the reconciliation sweep

    outbox = client.get('/api/einvoicing/outbox').get_json()['data']
    matching = [row for row in outbox if row['source_id'] == pre_sale['id']]
    assert matching == [], "a sale from before the feature was ever enabled must never be retroactively submitted"
