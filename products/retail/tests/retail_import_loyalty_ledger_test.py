"""Aura Retail -- an IMPORTED loyalty balance must be spendable.

`customers.loyalty_points` is an ACCUMULATOR COLUMN. The spendable balance is
the SUM of `loyalty_ledger` -- never the column. That is deliberate and
documented at every read site: schema.py's `_migrate_add_loyalty_ledger`
explains the two-till double-spend a column-based balance would reopen, and
`customer_loyalty_balance` (retail_api.py) says in its own comment that what
it returns is "exactly what the till itself will check".

Both live writers keep the two in step: create_sale writes the 'earn' ledger
row AND bumps the column in the same transaction (retail_api.py ~6661), and
create_return writes the clawback rows AND decrements the column (~8368).

THE IMPORT DID NOT. `api/import_api.py` writes `customers.loyalty_points`
directly -- on the INSERT branch and on the UPDATE branch -- and contains ZERO
references to `loyalty_ledger`. `loyalty_points` is an offered import column
with an example value of '150' in that file's own schema definition, so this
is a shipped, advertised path, not a corner.

WHAT THE SHOPKEEPER SAW. The Customers table and the customer detail card
both render `cu.loyalty_points` (subsystem-retail.js ~6084 / ~6213), so an
imported customer showed "150 pts" on screen. The till reads the ledger, which
had nothing, and refused the redemption with "Insufficient loyalty point
balance." Two screens, two answers, and the one that says no is the one
holding the customer's goods.

v27's migration backfilled ONE 'opening' row per customer with
`loyalty_points > 0`, which is why this is invisible on any balance that
predates the ledger: the gap only opens for a customer imported AFTER that
migration ran, and it never closes, because the migration does not run again.

WHAT THE FIX WRITES. An 'opening' row on insert, and on update an ADJUSTING
row for the difference between the imported figure and the ledger's current
sum -- so an import that sets a balance to 150 makes the spendable balance
150, whatever it was before. `entry_type='adjust'` is the value
`_migrate_add_loyalty_ledger`'s own docstring names for a correction that
belongs to no sale.

Self-contained bootstrap, per this directory's convention. CRITICAL: exactly
ONE pytest process per file (AUDIT-010).

Run:
    pytest products/retail/tests/retail_import_loyalty_ledger_test.py -v
"""
import csv
import io
import json
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_importloyalty_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(
    AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA),
    AURA_SITE_RELAY_ENABLED="0",
)
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402

seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")
os.environ.pop("AURA_RETAIL_DEMO_MODE", None)

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402
from database.schema import get_retail_conn  # noqa: E402

API = '/api/sub/retail'


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


@pytest.fixture
def client():
    email = f'importloyalty-{uuid.uuid4().hex[:8]}@test.local'
    password = 'ImportLoyaltyPW1'
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
    c.test_company_id = company_id
    return c


def _csv_bytes(rows, headers):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=headers)
    w.writeheader()
    for r in rows:
        w.writerow(r)
    return buf.getvalue().encode('utf-8')


def _import_customers(client, rows):
    """Drives the REAL /api/import/execute route with a real CSV, the same
    way retail_import_sync_test.py's own `_import` helper does -- not a
    direct call into import_api's internals."""
    headers = ['name', 'email', 'loyalty_points']
    mapping = {'name': 'name', 'email': 'email', 'loyalty_points': 'loyalty_points'}
    r = client.post("/api/import/execute", data={
        'system': 'retail', 'entity': 'customers',
        'mapping': json.dumps(mapping),
        'file': (io.BytesIO(_csv_bytes(rows, headers)), 'customers.csv'),
    }, content_type='multipart/form-data')
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['success'] is True, r.get_json()
    return r.get_json()


def _customer_by_email(cid, email):
    conn = get_retail_conn()
    row = conn.execute(
        "SELECT id, loyalty_points FROM customers WHERE company_id=? AND email=?", (cid, email)
    ).fetchone()
    conn.close()
    return row


def _ledger_sum(cid, customer_id):
    conn = get_retail_conn()
    total = conn.execute(
        "SELECT COALESCE(SUM(points_delta), 0) FROM loyalty_ledger WHERE company_id=? AND customer_id=?",
        (cid, customer_id)
    ).fetchone()[0] or 0
    conn.close()
    return total


def _api_balance(client, customer_id):
    """The route the till and the cashier both read."""
    r = client.get(f'{API}/customers/{customer_id}/loyalty')
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['balance']


# ── the defect ────────────────────────────────────────────────────────────────

def test_imported_points_reach_the_ledger_the_till_reads(client):
    """150 imported -> 150 spendable. Asserted through the API the cashier
    and the till both read, not by inspecting the table directly, so a fix
    that wrote the rows but left the read path disagreeing would still fail."""
    cid = client.test_company_id
    email = f'ledger-{uuid.uuid4().hex[:6]}@test.local'

    _import_customers(client, [{'name': 'Imported Regular', 'email': email, 'loyalty_points': '150'}])

    cust = _customer_by_email(cid, email)
    assert cust is not None, "precondition: the import created the customer"
    assert cust['loyalty_points'] == 150, (
        f"precondition: the column carries the imported figure, got {cust['loyalty_points']!r}")

    assert _ledger_sum(cid, cust['id']) == 150, (
        "the imported points must exist in the ledger, which is the only balance "
        f"create_sale will honour; ledger sums to {_ledger_sum(cid, cust['id'])!r}")
    assert _api_balance(client, cust['id']) == 150, (
        "the balance route the till reads must agree with the screen, got "
        f"{_api_balance(client, cust['id'])!r}")


def test_a_re_import_sets_the_balance_rather_than_doubling_it(client):
    """The UPDATE branch. Importing the SAME customer again with the same
    figure must leave the spendable balance at 150, not 300 -- the fix writes
    an ADJUSTING row for the difference from the ledger's current sum, so the
    balance lands on the imported figure whatever it was before.

    This is the half a naive "always append an opening row" fix gets wrong,
    and re-importing a corrected customer list is a completely ordinary thing
    for a shop to do."""
    cid = client.test_company_id
    email = f'reimport-{uuid.uuid4().hex[:6]}@test.local'
    row = {'name': 'Twice Imported', 'email': email, 'loyalty_points': '150'}

    _import_customers(client, [row])
    _import_customers(client, [row])

    cust = _customer_by_email(cid, email)
    assert cust['loyalty_points'] == 150, f"column: {cust['loyalty_points']!r}"
    assert _ledger_sum(cid, cust['id']) == 150, (
        f"a re-import must SET the balance, not add to it; got {_ledger_sum(cid, cust['id'])!r}")


def test_a_re_import_with_a_new_figure_moves_the_balance_to_it(client):
    """Corrected list: 150 -> 90. The spendable balance must follow the
    import, and the adjusting row must be a real -60 delta rather than a
    rewritten history (this table is append-only: "never edit/delete, only
    reverse")."""
    cid = client.test_company_id
    email = f'corrected-{uuid.uuid4().hex[:6]}@test.local'

    _import_customers(client, [{'name': 'Corrected', 'email': email, 'loyalty_points': '150'}])
    cust = _customer_by_email(cid, email)
    _import_customers(client, [{'name': 'Corrected', 'email': email, 'loyalty_points': '90'}])

    assert _ledger_sum(cid, cust['id']) == 90, (
        f"the balance must follow the corrected import, got {_ledger_sum(cid, cust['id'])!r}")
    assert _api_balance(client, cust['id']) == 90

    conn = get_retail_conn()
    rows = conn.execute(
        "SELECT points_delta FROM loyalty_ledger WHERE company_id=? AND customer_id=? ORDER BY id",
        (cid, cust['id'])).fetchall()
    conn.close()
    deltas = [r[0] for r in rows]
    assert deltas == [150, -60], (
        f"append-only: the correction is a new -60 row, not an edit; got {deltas!r}")


def test_importing_zero_points_writes_no_ledger_row(client):
    """A customer list with no loyalty column -- or a zero in it -- is the
    common case, and it must not litter the ledger with meaningless zero
    rows. Mirrors v27's own backfill, which only seeds `loyalty_points > 0`."""
    cid = client.test_company_id
    email = f'zero-{uuid.uuid4().hex[:6]}@test.local'

    _import_customers(client, [{'name': 'No Points', 'email': email, 'loyalty_points': '0'}])

    cust = _customer_by_email(cid, email)
    conn = get_retail_conn()
    count = conn.execute(
        "SELECT COUNT(*) FROM loyalty_ledger WHERE company_id=? AND customer_id=?",
        (cid, cust['id'])).fetchone()[0]
    conn.close()
    assert count == 0, f"a zero balance needs no ledger row, got {count}"
