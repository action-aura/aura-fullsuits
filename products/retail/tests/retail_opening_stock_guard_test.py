"""Aura Retail -- `create_product`'s opening stock cannot write a balance the
ledger has never heard of.

THE DEFECT. `create_product` (retail_api.py) wrote the client's
`initial_stock` STRAIGHT into `inventory_balances.quantity_on_hand`:

    INSERT OR IGNORE INTO inventory_balances (...) VALUES (?,?,?,?)
        ... data.get('initial_stock', 0)
    if data.get('initial_stock', 0) > 0:
        ... the only path that writes an inventory_movements row

`pid` is a freshly minted uuid4, so the OR IGNORE never ignores -- the row is
always inserted with whatever arrived, with no coercion, no negative check and
no epsilon floor. The movement row is written only when the figure is > 0.

Send `initial_stock: -10` and the balance becomes -10 with NO movement row
behind it, ever.

WHY THAT IS UNRECOVERABLE RATHER THAN MERELY WRONG. `core/retail/
stock_reconciliation.py` -- the thing that repairs a drifted balance from the
ledger -- SKIPS any (product, branch) key with no ledger history at all
(`_has_ledger_history` / `SKIP_NO_LEDGER_HISTORY`), because a balance with no
movement behind it is a key the ledger has never heard of. So this particular
corruption is precisely the shape the repair path is designed to refuse to
touch: silent, permanent, with no audit trail and no route back.

Every SIBLING writer of `inventory_balances` already guards this. `adjust_stock`
floors, `send_stock_transfer`/`receive_stock_transfer` refuse, and
`import_api.py`'s bulk stock declaration refuses this EXACT input before any
write (`if declared < -_QTY_TOLERANCE: stock_errors.append(...)`).
create_product's opening-stock path was simply never given the same treatment.

REACHABLE FROM THE UI, not just from a direct API call: the field is
`<input type="number" id="pm-stock" value="0" min="0">`
(subsystem-retail.js), and the submit handler reads `.value` and coerces with
a unary `+` without calling `checkValidity()`, so `min="0"` is advisory and a
typed `-10` reaches the server.

THE FIX REFUSES rather than floors. A shop cannot have started with less than
nothing -- that is `RetailLedgerDriftError`'s own argument, in this repo's own
words -- so a negative opening stock has no legitimate meaning, and silently
flooring it to 0 would hide an operator's mistake behind a product that looks
fine. Refusing matches import_api's precedent for the identical input.

Self-contained bootstrap; no shared conftest.py. CRITICAL: exactly ONE pytest
process per file (AUDIT-010).

Run:
    pytest products/retail/tests/retail_opening_stock_guard_test.py -v
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_openstock_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(
    AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA),
    AURA_SITE_RELAY_ENABLED="0",
)
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402

seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")

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
    email = f'openstock-{uuid.uuid4().hex[:8]}@test.local'
    password = 'OpenStockPW1'
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


def _create_product(client, initial_stock, sku=None):
    return client.post(f'{API}/products', json={
        'sku': sku or f'OS-{uuid.uuid4().hex[:8]}',
        'name': 'Opening Stock Widget',
        'sell_price': 10.0, 'cost_price': 5.0, 'tax_rate': 0,
        'initial_stock': initial_stock,
    })


def _balance_rows(cid, product_id):
    conn = get_retail_conn()
    rows = conn.execute(
        "SELECT quantity_on_hand FROM inventory_balances WHERE company_id=? AND product_id=?",
        (cid, product_id)).fetchall()
    conn.close()
    return [r[0] for r in rows]


def _movement_rows(cid, product_id):
    conn = get_retail_conn()
    rows = conn.execute(
        "SELECT movement_type, quantity FROM inventory_movements WHERE company_id=? AND product_id=?",
        (cid, product_id)).fetchall()
    conn.close()
    return [(r[0], r[1]) for r in rows]


def _product_by_sku(cid, sku):
    conn = get_retail_conn()
    row = conn.execute(
        "SELECT id FROM products WHERE company_id=? AND sku=?", (cid, sku)).fetchone()
    conn.close()
    return row[0] if row else None


# ── the defect ────────────────────────────────────────────────────────────────

def test_a_negative_opening_stock_is_refused(client):
    """A shop cannot have started with less than nothing -- this repo's own
    `RetailLedgerDriftError` makes exactly that argument. Refused outright
    rather than floored to 0, matching import_api's handling of the identical
    input, because flooring hides an operator's mistake behind a product that
    then looks fine."""
    sku = f'OS-NEG-{uuid.uuid4().hex[:8]}'
    r = _create_product(client, -10, sku=sku)

    assert r.status_code == 400, (
        f"a negative opening stock must be refused, got {r.status_code}: {r.get_json()}")

    # And the refusal must be TOTAL -- no half-created product left behind.
    assert _product_by_sku(client.test_company_id, sku) is None, (
        "the whole request is refused, so no product row may survive it")


def test_a_refused_negative_leaves_no_balance_the_ledger_cannot_explain(client):
    """THE REASON THIS MATTERS, asserted directly rather than implied.

    Before the fix, `initial_stock: -10` wrote `quantity_on_hand = -10` with no
    `inventory_movements` row at all -- and `stock_reconciliation.py` SKIPS any
    key with no ledger history (`SKIP_NO_LEDGER_HISTORY`), so that balance
    could never be repaired from the ledger. Permanent, silent, untraceable.

    This asserts the invariant the fix restores: every balance row this route
    creates is either zero, or has a movement row behind it."""
    sku = f'OS-INV-{uuid.uuid4().hex[:8]}'
    _create_product(client, -10, sku=sku)

    pid = _product_by_sku(client.test_company_id, sku)
    if pid is not None:  # pragma: no cover -- only reachable pre-fix
        balances = _balance_rows(client.test_company_id, pid)
        movements = _movement_rows(client.test_company_id, pid)
        assert not any(b < 0 for b in balances), (
            f"a negative balance was written with movements={movements!r} behind it -- "
            "stock_reconciliation skips keys with no ledger history, so this is "
            "permanent")


def test_a_non_numeric_opening_stock_is_refused_not_a_500(client):
    """`data.get('initial_stock', 0) > 0` compares a str against an int, which
    raises TypeError in Python 3 -- a 500 for what is plainly a bad request.
    Coerced and refused as a 4xx instead."""
    r = _create_product(client, 'abc')
    assert 400 <= r.status_code < 500, (
        f"a non-numeric opening stock is a bad request, not a server error; got {r.status_code}")


# ── the allow half ────────────────────────────────────────────────────────────

def test_a_positive_opening_stock_still_writes_both_the_balance_and_its_movement(client):
    """THE ALLOW HALF. The guard must not break the ordinary case: a real
    opening count still lands in the balance AND gets the `opening_stock`
    movement row that makes it explicable to the reconciler."""
    sku = f'OS-POS-{uuid.uuid4().hex[:8]}'
    r = _create_product(client, 25, sku=sku)
    assert r.status_code == 200, r.get_json()

    pid = _product_by_sku(client.test_company_id, sku)
    assert _balance_rows(client.test_company_id, pid) == [25], (
        f"got {_balance_rows(client.test_company_id, pid)!r}")
    assert ('opening_stock', 25) in _movement_rows(client.test_company_id, pid), (
        f"the balance needs a movement behind it; got "
        f"{_movement_rows(client.test_company_id, pid)!r}")


def test_zero_opening_stock_is_allowed_and_writes_no_movement(client):
    """The overwhelmingly common case -- a product created before its first
    delivery. A zero balance with no movement is explicable (nothing has
    happened yet) and is what the route has always written."""
    sku = f'OS-ZERO-{uuid.uuid4().hex[:8]}'
    r = _create_product(client, 0, sku=sku)
    assert r.status_code == 200, r.get_json()

    pid = _product_by_sku(client.test_company_id, sku)
    assert _balance_rows(client.test_company_id, pid) == [0]
    assert _movement_rows(client.test_company_id, pid) == []


def test_opening_stock_omitted_entirely_still_works(client):
    """A caller that never sends the field at all -- the API's own default."""
    sku = f'OS-NONE-{uuid.uuid4().hex[:8]}'
    r = client.post(f'{API}/products', json={
        'sku': sku, 'name': 'No Stock Field', 'sell_price': 10.0, 'tax_rate': 0,
    })
    assert r.status_code == 200, r.get_json()
    pid = _product_by_sku(client.test_company_id, sku)
    assert _balance_rows(client.test_company_id, pid) == [0]
