"""Aura Retail -- AR/AP total_receivable / total_payable aggregation regression.

Covers a bug where `customers_receivables()` / `suppliers_payables()`
(products/retail/backend/api/retail_api.py) computed their totals as an
UNFILTERED `SUM(credit_balance)` across every party, while the `rows` list
returned alongside it only includes parties with `credit_balance > 0.005`.

A party can end up with a *negative* (overpaid) credit_balance because
customer_payment()/supplier_payment() only validate `amount > 0` -- they
never cap the payment at the party's outstanding balance. When that
happens, the unfiltered SUM silently nets the overpaid party's negative
balance against everyone else's genuine debt, so total_receivable /
total_payable no longer equals the sum of the rows actually shown to the
user (the Android Receivables/Payables screens render the mismatched total
directly above a list that sums to something else).

Fix: filter the total the same way as the rows query (`credit_balance >
0.005`), so the total always equals sum(rows).

This file follows the same self-contained bootstrap convention as
`retail_customer_sync_test.py` (no shared conftest.py exists for
products/retail/tests/ -- confirmed by inspection): its own temp app-data
dir, its own license seed, its own Flask app boot, its own `client`/
`db_conn` fixtures local to this file.

Run:
    pytest products/retail/tests/retail_ar_ap_totals_test.py -v
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_arap_totals_"))
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
from database.schema import get_retail_conn  # noqa: E402


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


@pytest.fixture
def client():
    """A fresh, logged-in Flask test client for its own newly-created
    company -- one company per test, so credit_balance assertions never see
    another test's rows."""
    email = f'arap-admin-{uuid.uuid4().hex[:8]}@test.local'
    password = 'ArApTotalsPW1'
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
    return c


@pytest.fixture
def db_conn():
    """A real connection to the (shared, file-backed) retail database this
    process is using -- used only to seed a genuine owed balance directly,
    the same value a completed credit sale would have left behind."""
    conn = get_retail_conn()
    yield conn
    conn.close()


def test_total_receivable_ignores_an_overpaid_customers_negative_balance(client, db_conn):
    # Customer A genuinely owes $1000 (as a completed credit sale would leave
    # behind).
    owing = client.post('/api/sub/retail/customers', json={'name': 'Owes Money'}).get_json()['data']['id']
    db_conn.execute("UPDATE customers SET credit_balance=1000.00 WHERE id=?", (owing,))
    db_conn.commit()

    # Customer B starts at a $0 balance and is overpaid by $200 through the
    # real payment route -- exactly the path the finding calls out:
    # customer_payment() validates amount>0 only, never caps it at the
    # outstanding balance, so this reaches credit_balance=-200.
    overpaid = client.post('/api/sub/retail/customers', json={'name': 'Overpaid'}).get_json()['data']['id']
    pay = client.post(f'/api/sub/retail/customers/{overpaid}/payments', json={'amount': 200})
    assert pay.status_code == 200, pay.get_json()
    assert pay.get_json()['data']['new_balance'] == -200.0

    resp = client.get('/api/sub/retail/customers/receivables')
    assert resp.status_code == 200
    body = resp.get_json()

    # The overpaid customer must not appear in the list (matches the
    # existing >0.005 filter on the rows query)...
    row_ids = [r['id'] for r in body['data']]
    assert owing in row_ids
    assert overpaid not in row_ids

    # ...and the total must equal what the list actually sums to, not a
    # value silently reduced by the excluded customer's negative balance.
    assert body['total_receivable'] == 1000.0
    assert body['total_receivable'] == sum(r['credit_balance'] for r in body['data'])


def test_total_payable_ignores_an_overpaid_suppliers_negative_balance(client, db_conn):
    owing = client.post('/api/sub/retail/suppliers', json={'name': 'Owed Money'}).get_json()['data']['id']
    db_conn.execute("UPDATE suppliers SET credit_balance=500.00 WHERE id=?", (owing,))
    db_conn.commit()

    overpaid = client.post('/api/sub/retail/suppliers', json={'name': 'Overpaid Supplier'}).get_json()['data']['id']
    pay = client.post(f'/api/sub/retail/suppliers/{overpaid}/payments', json={'amount': 75})
    assert pay.status_code == 200, pay.get_json()
    assert pay.get_json()['data']['new_balance'] == -75.0

    resp = client.get('/api/sub/retail/suppliers/payables')
    assert resp.status_code == 200
    body = resp.get_json()

    row_ids = [r['id'] for r in body['data']]
    assert owing in row_ids
    assert overpaid not in row_ids

    assert body['total_payable'] == 500.0
    assert body['total_payable'] == sum(r['credit_balance'] for r in body['data'])
