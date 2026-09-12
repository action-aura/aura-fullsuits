"""
Aura Retail -- stock-accuracy sweep regression suite.

`inventory_balances.quantity_on_hand` is a mutable STORED cache written by
five independent code paths, and until this branch nothing ever checked it
against the `inventory_movements` ledger it is supposed to be a cache of.
Two of those writers were outright broken; the reconciliation that would
have EXPOSED either of them did not exist at all. This suite pins all of
it down:

 1. receive_purchase_order was a check-then-act race with no write lock and
    no status guard on its final UPDATE -- two concurrent Receive requests
    both read 'pending' and both added the PO's quantities, so a 10-unit
    delivery landed as 20 units on hand plus two 'purchase_in' ledger rows.
    Test 1 drives the real race with two threads and a deterministic stall
    inside the handler.
 2. import_api's product handler SET the balance absolutely and wrote NO
    movement row -- re-importing an unchanged sheet resurrected every unit
    sold since the first import. Test 2 walks the exact reported sequence
    (import 50, sell 10, re-import the same sheet) and additionally proves
    a CHANGED sheet still posts a real, signed ledger row.
 3. adjust_stock always wrote to the company's FIRST branch by id, never
    checked the product existed, and could drive a balance negative.
    Tests 3-5.
 4. Nothing reconciled the cache against the ledger. Tests 6-7 corrupt a
    balance behind the API's back the way a real bug would, and require the
    new maintenance routes to both SEE and REPAIR it.
 5. Voiding a SALE receipt deleted the money and left the goods gone --
    for a walk-in there was no party to bill at all, and for a NAMED
    customer the re-billing left customer_statement() and
    customers.credit_balance reporting two different debts. Tests 8-9.
 6. The delta-based import introduced its own way to break the balance:
    a LOWER re-declaration applied blind drives on hand negative, a
    declared 0 was silently discarded, and the column was still labelled
    "Current Stock Qty" while the backend read it as a cumulative opening
    declaration. Tests 10-13 (13 also pins the en/ar catalogs).
 7. adjust_stock's zero floor was an exact float comparison, so a
    fractional-unit product could never be zeroed out. Test 14.
 8. The reconciliation REPORT dumps every product name, SKU, branch and
    quantity in the company and carried only login + subsystem, while its
    repair twin required company-admin. Test 15.
 9. Launch-readiness Phase 7 stage 7c-i (docs/launch-readiness/
    phase7-offline-ux.md, "DO block: receiving a purchase order") added a
    guard refusing receipt whenever this device was behind the shared
    staleness threshold, reasoning that PO status never syncs so a stale
    device could not tell whether another device had already received the
    same PO. RETRACTED (2026-08-29): that premise is false. It is not just
    `status` that never syncs -- the WHOLE `purchase_orders` table is
    local-only and never queued to the sync outbox at all (sync_service.py's
    module docstring; accept_reorder_request's docstring in retail_api.py
    says the same for every PO this route ever creates). A PO exists on
    exactly ONE device, so there is no second device to double-receive it
    on, and the guard blocked a hazard that could not occur while refusing
    real deliveries on a shop's own device the moment it fell behind on
    sync for something unrelated. The guard is removed; test 1 above (the
    real, reachable same-device race -- BEGIN IMMEDIATE plus the
    conditional status UPDATE) is untouched and still passes. Tests 16-20.

Self-contained bootstrap, matching every other file in this directory (no
shared conftest.py exists here): own temp app-data dir, own license seed,
own Flask app boot. Run:

    pytest products/retail/tests/retail_stock_accuracy_test.py -v
"""
import csv
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

import pytest

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_stockacc_"))
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
from commercial_runtime.security.passwords import hash_password  # noqa: E402
from database.schema import get_retail_conn  # noqa: E402
from api import retail_api as _retail_api  # noqa: E402


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


# ── Fixtures / helpers ────────────────────────────────────────────────────

def _new_company():
    """A fresh company with one admin user. One company per test keeps the
    company-scoped reconciliation report free of other tests' rows."""
    email = f"stockacc-{uuid.uuid4().hex[:10]}@test.local"
    password = "StockAccPW1"
    company_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, require_password_change) "
        "VALUES (?,?,?,?,?,?,?,0)",
        (str(uuid.uuid4()), company_id, "EMP-0001", email, hash_password(password), "admin", "active"),
    )
    conn.commit()
    conn.close()
    return company_id, email, password


def _login(email, password):
    c = app.test_client()
    r = c.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.get_json()
    # Forces _ensure_credit_schema/doc_sequences to exist before any test
    # starts timing a concurrent path.
    c.get("/api/sub/retail/settings/tax")
    return c


@pytest.fixture
def company():
    company_id, email, password = _new_company()
    return company_id, _login(email, password)


def _create_product(client, sku=None, price=10.0, initial_stock=0):
    sku = sku or f"SA-{uuid.uuid4().hex[:8]}"
    r = client.post("/api/sub/retail/products", json={
        "name": f"Stock Item {sku}", "sku": sku, "sell_price": price,
        "cost_price": price / 2, "tax_rate": 0, "initial_stock": initial_stock,
    })
    assert r.status_code == 200, r.get_json()
    return r.get_json()["data"]["id"], sku


def _balance(company_id, product_id, branch_id=None):
    conn = get_retail_conn()
    try:
        if branch_id is None:
            row = conn.execute(
                "SELECT COALESCE(SUM(quantity_on_hand),0) AS q FROM inventory_balances "
                "WHERE company_id=? AND product_id=?", (company_id, product_id)).fetchone()
        else:
            row = conn.execute(
                "SELECT COALESCE(SUM(quantity_on_hand),0) AS q FROM inventory_balances "
                "WHERE company_id=? AND product_id=? AND branch_id=?",
                (company_id, product_id, branch_id)).fetchone()
        return float(row["q"])
    finally:
        conn.close()


def _ledger(company_id, product_id):
    conn = get_retail_conn()
    try:
        row = conn.execute(
            "SELECT COALESCE(SUM(quantity),0) AS q FROM inventory_movements "
            "WHERE company_id=? AND product_id=?", (company_id, product_id)).fetchone()
        return float(row["q"])
    finally:
        conn.close()


def _movements(company_id, product_id):
    conn = get_retail_conn()
    try:
        rows = conn.execute(
            "SELECT movement_type, quantity, reference, notes FROM inventory_movements "
            "WHERE company_id=? AND product_id=? ORDER BY id", (company_id, product_id)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _cashier_client(company_id):
    """A NON-admin retail user inside an existing company: role='cashier',
    with the retail permission row mt_require_subsystem('retail') demands so
    the request reaches the route's own authorization instead of bouncing
    off the subsystem gate first."""
    email = f"cashier-{uuid.uuid4().hex[:10]}@test.local"
    password = "CashierPW1"
    user_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, require_password_change) "
        "VALUES (?,?,?,?,?,?,?,0)",
        (user_id, company_id, f"EMP-{uuid.uuid4().hex[:6]}", email, hash_password(password), "cashier", "active"),
    )
    conn.execute(
        "INSERT INTO user_permissions (id, user_id, subsystem, access_level) VALUES (?,?,?,?)",
        (str(uuid.uuid4()), user_id, "retail", "full"),
    )
    conn.commit()
    conn.close()
    return _login(email, password)


def _add_branch(company_id, name):
    conn = get_retail_conn()
    try:
        cur = conn.cursor()
        cur.execute("INSERT INTO branches (company_id,name) VALUES (?,?)", (company_id, name))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _csv_bytes(rows, headers):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=headers)
    w.writeheader()
    for r in rows:
        w.writerow(r)
    return buf.getvalue().encode("utf-8")


def _import_products(client, rows, headers, mapping):
    r = client.post("/api/import/execute", data={
        "system": "retail", "entity": "products",
        "mapping": json.dumps(mapping),
        "file": (io.BytesIO(_csv_bytes(rows, headers)), "products.csv"),
    }, content_type="multipart/form-data")
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["success"] is True, r.get_json()
    return r.get_json()


# ═════════════════════════════════════════════════════════════════════════
# 1. Double-submitted PO receive
# ═════════════════════════════════════════════════════════════════════════

def test_double_submitted_po_receive_adds_stock_exactly_once(company, monkeypatch):
    """The real race, driven for real: two concurrent Receive requests for
    one 10-unit PO.

    Determinism comes from stalling the FIRST request inside the handler,
    at a point both the broken and the fixed version reach identically
    (the PO_RECEIVED audit write, which sits after the stock writes and
    before the commit in both). That stall is the window the second
    request needs.

      * Broken code: request B takes no lock and reads the PO before A has
        committed, sees 'pending', and adds the 10 units a second time.
      * Fixed code: B blocks on BEGIN IMMEDIATE at the top of the handler,
        so it only reads the PO after A commits, sees 'received', and
        exits 409 having written nothing.
    """
    company_id, client = company
    pid, _sku = _create_product(client, initial_stock=0)

    sup = client.post("/api/sub/retail/suppliers", json={"name": "Race Supplier"})
    assert sup.status_code == 200, sup.get_json()
    supplier_id = sup.get_json()["data"]["id"]

    po = client.post("/api/sub/retail/purchase-orders", json={
        "supplier_id": supplier_id,
        "items": [{"product_id": pid, "quantity": 10, "unit_cost": 5}],
    })
    assert po.status_code == 200, po.get_json()
    po_id = po.get_json()["data"]["id"]

    real_audit = _retail_api._audit
    stalled = threading.Event()

    def stalling_audit(conn, action, entity, entity_id, details=''):
        # Only the FIRST receive stalls; a second one (or any other audit
        # write in this request) must run at full speed.
        if action == 'PO_RECEIVED' and not stalled.is_set():
            stalled.set()
            time.sleep(1.5)
        return real_audit(conn, action, entity, entity_id, details)

    monkeypatch.setattr(_retail_api, '_audit', stalling_audit)

    results = {}

    def receive(tag, http):
        r = http.post(f"/api/sub/retail/purchase-orders/{po_id}/receive", json={})
        results[tag] = r.status_code

    # Two independent clients: two sessions, two connections, one database.
    client_b = _login(*_new_company_login_for(company_id))
    t_a = threading.Thread(target=receive, args=("a", client))
    t_b = threading.Thread(target=receive, args=("b", client_b))
    t_a.start()
    time.sleep(0.25)   # A is inside the handler and stalled before B starts
    t_b.start()
    t_a.join(timeout=60)
    t_b.join(timeout=60)

    assert sorted(results.values()) == [200, 409], results

    # The whole point: ONE delivery, ONE ledger row, ONE balance movement.
    purchase_ins = [m for m in _movements(company_id, pid) if m["movement_type"] == "purchase_in"]
    assert len(purchase_ins) == 1, purchase_ins
    assert _balance(company_id, pid) == 10.0
    assert _ledger(company_id, pid) == _balance(company_id, pid)


def _new_company_login_for(company_id):
    """A SECOND admin user inside an EXISTING company, so the two racing
    clients share one company's data (the real double-click / two-device
    scenario) rather than each getting their own."""
    email = f"stockacc2-{uuid.uuid4().hex[:10]}@test.local"
    password = "StockAccPW2"
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, require_password_change) "
        "VALUES (?,?,?,?,?,?,?,0)",
        (str(uuid.uuid4()), company_id, "EMP-0002", email, hash_password(password), "admin", "active"),
    )
    conn.commit()
    conn.close()
    return email, password


# ═════════════════════════════════════════════════════════════════════════
# 2. Re-import must not resurrect sold units, and must leave a ledger trail
# ═════════════════════════════════════════════════════════════════════════

def test_reimport_does_not_resurrect_sold_units_and_writes_a_movement(company):
    """The exact reported sequence: import 50, sell 10, re-import the same
    sheet. The balance must stay at 40, and the ledger must agree with it
    at every step -- which requires the import to have written a movement
    row for the stock it declared in the first place."""
    company_id, client = company
    sku = f"IMP-{uuid.uuid4().hex[:8]}"
    headers = ["Product Name", "SKU", "Selling Price", "Stock"]
    mapping = {"name": "Product Name", "sku": "SKU",
               "sell_price": "Selling Price", "initial_stock": "Stock"}

    def sheet(stock):
        return [{"Product Name": "Imported Stock Item", "SKU": sku,
                 "Selling Price": "10", "Stock": str(stock)}]

    # ── first import: 50 declared ────────────────────────────────────────
    assert _import_products(client, sheet(50), headers, mapping)["imported"] == 1
    conn = get_retail_conn()
    pid = conn.execute("SELECT id FROM products WHERE company_id=? AND sku=?",
                       (company_id, sku)).fetchone()["id"]
    conn.close()

    assert _balance(company_id, pid) == 50.0
    # The half of the bug that made everything downstream unfixable: the
    # import used to write NO movement at all, so the ledger said 0 while
    # the balance said 50 from the very first import.
    assert _ledger(company_id, pid) == 50.0
    opening = [m for m in _movements(company_id, pid) if m["movement_type"] == "opening_stock"]
    assert len(opening) == 1 and opening[0]["quantity"] == 50.0

    # ── sell 10 ──────────────────────────────────────────────────────────
    sale = client.post("/api/sub/retail/sales", json={
        "items": [{"product_id": pid, "quantity": 10}],
        "payment_method": "cash", "amount_paid": 100,
    })
    assert sale.status_code == 200, sale.get_json()
    assert _balance(company_id, pid) == 40.0
    assert _ledger(company_id, pid) == 40.0

    # ── re-import the SAME sheet ─────────────────────────────────────────
    result = _import_products(client, sheet(50), headers, mapping)
    assert result["updated"] == 1
    # The headline assertion: the ten sold units stay sold.
    assert _balance(company_id, pid) == 40.0, "re-import resurrected sold units"
    assert _ledger(company_id, pid) == 40.0
    # An unchanged sheet declares nothing new, so it posts nothing.
    assert len(_movements(company_id, pid)) == 2

    # ── re-import with a CHANGED declaration: 50 -> 60 ────────────────────
    assert _import_products(client, sheet(60), headers, mapping)["updated"] == 1
    corrections = [m for m in _movements(company_id, pid) if m["reference"] == "IMPORT"]
    assert len(corrections) == 1, corrections
    assert corrections[0]["quantity"] == 10.0
    assert corrections[0]["movement_type"] == "stock_in"
    # +10 on top of the 40 actually on hand -- never a jump straight to 60.
    assert _balance(company_id, pid) == 50.0
    assert _ledger(company_id, pid) == 50.0


# ═════════════════════════════════════════════════════════════════════════
# 3-5. adjust_stock: branch, product existence, negative floor
# ═════════════════════════════════════════════════════════════════════════

def test_stock_adjustment_lands_on_the_branch_the_caller_names(company):
    """Two-branch install: an adjustment for branch 2 must credit branch 2,
    not whichever branch happens to sort first by id."""
    company_id, client = company
    pid, _sku = _create_product(client, initial_stock=0)

    conn = get_retail_conn()
    main_id = conn.execute("SELECT id FROM branches WHERE company_id=? ORDER BY id LIMIT 1",
                           (company_id,)).fetchone()["id"]
    conn.close()
    second_id = _add_branch(company_id, "Downtown")
    assert second_id != main_id

    r = client.post(f"/api/sub/retail/products/{pid}/stock-adjust",
                    json={"quantity": 5, "reason": "Stock count", "branch_id": second_id})
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["branch_id"] == second_id
    assert body["new_stock"] == 5.0

    assert _balance(company_id, pid, second_id) == 5.0
    assert _balance(company_id, pid, main_id) == 0.0, "adjustment landed on the wrong branch"
    # And the POS at that branch can now actually sell it.
    sale = client.post("/api/sub/retail/sales", json={
        "items": [{"product_id": pid, "quantity": 5}],
        "branch_id": second_id, "payment_method": "cash", "amount_paid": 50,
    })
    assert sale.status_code == 200, sale.get_json()

    # A branch that is not this company's is refused outright, never
    # silently redirected to the default branch.
    other_company_id, other_email, other_password = _new_company()
    _login(other_email, other_password).post("/api/sub/retail/products", json={
        "name": "Other", "sku": f"OTH-{uuid.uuid4().hex[:8]}", "sell_price": 1})
    conn = get_retail_conn()
    foreign_bid = conn.execute("SELECT id FROM branches WHERE company_id=? LIMIT 1",
                               (other_company_id,)).fetchone()["id"]
    conn.close()
    r = client.post(f"/api/sub/retail/products/{pid}/stock-adjust",
                    json={"quantity": 5, "branch_id": foreign_bid})
    assert r.status_code == 404, r.get_json()


def test_stock_adjustment_rejects_an_unknown_product(company):
    """A stale or mistyped product id used to create an orphan balance +
    movement pair for a product that is not in this company."""
    company_id, client = company
    ghost = str(uuid.uuid4())
    r = client.post(f"/api/sub/retail/products/{ghost}/stock-adjust", json={"quantity": 20})
    assert r.status_code == 404, r.get_json()
    assert _balance(company_id, ghost) == 0.0
    assert _movements(company_id, ghost) == []


def test_stock_adjustment_cannot_drive_a_balance_negative(company):
    """create_sale refuses to oversell; an adjustment must not be able to
    produce the same negative balance behind its back."""
    company_id, client = company
    pid, _sku = _create_product(client, initial_stock=5)

    r = client.post(f"/api/sub/retail/products/{pid}/stock-adjust",
                    json={"quantity": -100, "reason": "Damaged"})
    assert r.status_code == 400, r.get_json()
    assert _balance(company_id, pid) == 5.0
    assert _ledger(company_id, pid) == 5.0

    # Removing exactly what IS there still works -- the floor is at zero,
    # not one short of it.
    ok = client.post(f"/api/sub/retail/products/{pid}/stock-adjust",
                     json={"quantity": -5, "reason": "Damaged"})
    assert ok.status_code == 200, ok.get_json()
    assert _balance(company_id, pid) == 0.0


# ═════════════════════════════════════════════════════════════════════════
# 6-7. Reconciliation: see the drift, then repair it
# ═════════════════════════════════════════════════════════════════════════

def _corrupt_balance(company_id, product_id, delta):
    """Move the cached balance behind the API's back -- exactly what the
    two broken writers did, reproduced without depending on either of
    them still being broken."""
    conn = get_retail_conn()
    try:
        conn.execute(
            "UPDATE inventory_balances SET quantity_on_hand = quantity_on_hand + ? "
            "WHERE company_id=? AND product_id=?", (delta, company_id, product_id))
        conn.commit()
    finally:
        conn.close()


def test_reconciliation_detects_a_deliberately_corrupted_balance(company):
    company_id, client = company
    pid, sku = _create_product(client, initial_stock=20)
    sale = client.post("/api/sub/retail/sales", json={
        "items": [{"product_id": pid, "quantity": 3}],
        "payment_method": "cash", "amount_paid": 30,
    })
    assert sale.status_code == 200, sale.get_json()

    # Clean books first: nothing to report on a healthy company.
    clean = client.get("/api/sub/retail/inventory/reconciliation")
    assert clean.status_code == 200, clean.get_json()
    assert clean.get_json()["data"]["drift_count"] == 0, clean.get_json()

    _corrupt_balance(company_id, pid, 7)

    r = client.get("/api/sub/retail/inventory/reconciliation")
    assert r.status_code == 200, r.get_json()
    data = r.get_json()["data"]
    assert data["drift_count"] == 1, data
    row = data["rows"][0]
    assert row["product_id"] == pid
    assert row["sku"] == sku
    assert row["stored_balance"] == 24.0
    assert row["ledger_balance"] == 17.0
    assert row["drift"] == 7.0
    assert row["repairable"] is True
    assert data["net_drift"] == 7.0


def test_reconciliation_repair_rewrites_the_cache_from_the_ledger(company):
    company_id, client = company
    pid, _sku = _create_product(client, initial_stock=20)
    _corrupt_balance(company_id, pid, -6)
    assert _balance(company_id, pid) == 14.0

    # Confirmation token is mandatory, exactly like demo-wipe's.
    unconfirmed = client.post("/api/sub/retail/inventory/reconciliation/repair", json={})
    assert unconfirmed.status_code == 400, unconfirmed.get_json()
    assert _balance(company_id, pid) == 14.0

    r = client.post("/api/sub/retail/inventory/reconciliation/repair",
                    json={"confirm": f"RECONCILE-{company_id}"})
    assert r.status_code == 200, r.get_json()
    data = r.get_json()["data"]
    assert data["repaired_count"] == 1, data
    assert data["skipped_count"] == 0

    assert _balance(company_id, pid) == 20.0
    assert _ledger(company_id, pid) == 20.0
    # The repair rewrites the CACHE only -- it must never fabricate a
    # movement, which would move the very total being reconciled against.
    assert len(_movements(company_id, pid)) == 1

    after = client.get("/api/sub/retail/inventory/reconciliation")
    assert after.get_json()["data"]["drift_count"] == 0

    audit = get_retail_conn()
    try:
        rows = audit.execute(
            "SELECT details FROM audit_log WHERE company_id=? AND action='STOCK_RECONCILED'",
            (company_id,)).fetchall()
    finally:
        audit.close()
    assert len(rows) == 1
    assert "14.0" in rows[0]["details"] and "20.0" in rows[0]["details"]


# ═════════════════════════════════════════════════════════════════════════
# 8-9. Voiding a SALE receipt (walk-in and named customer alike)
# ═════════════════════════════════════════════════════════════════════════

def test_voiding_a_walkin_sale_receipt_is_refused(company):
    """Voiding a walk-in sale's cash receipt reversed nothing: not the
    sale, not the stock, and there is no party to bill. Refused now, with
    the operation that DOES reverse both named in the message."""
    company_id, client = company
    pid, _sku = _create_product(client, initial_stock=10)
    sale = client.post("/api/sub/retail/sales", json={
        "items": [{"product_id": pid, "quantity": 4}],
        "payment_method": "cash", "amount_paid": 40,
    })
    assert sale.status_code == 200, sale.get_json()
    sale_id = sale.get_json()["data"]["id"]

    conn = get_retail_conn()
    try:
        pay = conn.execute(
            "SELECT id, party_id FROM payments WHERE company_id=? AND related_type='sale' AND related_id=?",
            (company_id, sale_id)).fetchone()
    finally:
        conn.close()
    assert pay is not None and pay["party_id"] is None  # walk-in: no party

    r = client.post(f"/api/sub/retail/payments/{pay['id']}/void", json={"reason": "mistake"})
    assert r.status_code == 409, r.get_json()
    assert "return" in r.get_json()["message"].lower()

    # Nothing moved: the receipt is still active and the stock is still out.
    conn = get_retail_conn()
    try:
        status = conn.execute("SELECT status FROM payments WHERE id=?", (pay["id"],)).fetchone()["status"]
    finally:
        conn.close()
    assert status == "active"
    assert _balance(company_id, pid) == 6.0
    assert _ledger(company_id, pid) == 6.0


def test_voiding_a_named_customer_sale_receipt_is_refused_too(company):
    """The other half of the same defect. A NAMED customer's sale receipt
    was still voidable, which deleted the cash and re-billed the customer
    via _adjust_credit while the goods stayed gone -- and left the two
    screens that report the debt disagreeing, because `sales.amount_paid`
    is untouched: customer_statement() drops the charge (it filters on
    `total - amount_paid > 0.005`) AND the receipt (it filters on active
    payments), computing a zero running balance against a non-zero
    credit_balance."""
    company_id, client = company
    cust = client.post("/api/sub/retail/customers", json={"name": "Named Buyer"})
    assert cust.status_code == 200, cust.get_json()
    cust_id = cust.get_json()["data"]["id"]

    pid, _sku = _create_product(client, initial_stock=10)
    sale = client.post("/api/sub/retail/sales", json={
        "customer_id": cust_id,
        "items": [{"product_id": pid, "quantity": 4}],
        "payment_method": "cash", "amount_paid": 40,
    })
    assert sale.status_code == 200, sale.get_json()
    sale_id = sale.get_json()["data"]["id"]

    conn = get_retail_conn()
    try:
        pay = conn.execute(
            "SELECT id, party_id FROM payments WHERE company_id=? AND related_type='sale' AND related_id=?",
            (company_id, sale_id)).fetchone()
        before = conn.execute("SELECT COALESCE(credit_balance,0) AS b FROM customers WHERE id=?",
                              (cust_id,)).fetchone()["b"]
    finally:
        conn.close()
    assert pay is not None and pay["party_id"] == cust_id  # the NAMED half

    r = client.post(f"/api/sub/retail/payments/{pay['id']}/void", json={"reason": "mistake"})
    assert r.status_code == 409, r.get_json()
    assert "return" in r.get_json()["message"].lower()

    conn = get_retail_conn()
    try:
        row = conn.execute("SELECT status FROM payments WHERE id=?", (pay["id"],)).fetchone()
        after = conn.execute("SELECT COALESCE(credit_balance,0) AS b FROM customers WHERE id=?",
                             (cust_id,)).fetchone()["b"]
    finally:
        conn.close()
    assert row["status"] == "active"
    assert float(after) == float(before), "void re-billed the customer for goods it did not return"
    assert _balance(company_id, pid) == 6.0

    # A customer-ACCOUNT payment carries related_type NULL -- money only, no
    # goods and no sales row to contradict -- so it stays voidable. The
    # refusal above must be aimed at sale receipts, not at voids in general.
    client.post(f"/api/sub/retail/customers/{cust_id}/payments", json={"amount": 5})
    conn = get_retail_conn()
    try:
        acct_pay = conn.execute(
            "SELECT id FROM payments WHERE company_id=? AND party_id=? AND related_type IS NULL",
            (company_id, cust_id)).fetchone()
    finally:
        conn.close()
    assert acct_pay is not None
    ok = client.post(f"/api/sub/retail/payments/{acct_pay['id']}/void", json={"reason": "keyed twice"})
    assert ok.status_code == 200, ok.get_json()


# ═════════════════════════════════════════════════════════════════════════
# 10-13. The import stock column: floor, contract, and blank-vs-zero
# ═════════════════════════════════════════════════════════════════════════

_STOCK_HEADERS = ["Product Name", "SKU", "Selling Price", "Stock"]
_STOCK_MAPPING = {"name": "Product Name", "sku": "SKU",
                  "sell_price": "Selling Price", "initial_stock": "Stock"}


def _stock_sheet(sku, stock):
    """One product row. `stock` is written to the cell verbatim, so passing
    "" produces a genuinely BLANK cell and 0 produces a typed zero -- the
    exact distinction the handler is required to honour."""
    return [{"Product Name": "Imported Stock Item", "SKU": sku,
             "Selling Price": "10", "Stock": str(stock)}]


def _pid_for_sku(company_id, sku):
    conn = get_retail_conn()
    try:
        return conn.execute("SELECT id FROM products WHERE company_id=? AND sku=?",
                            (company_id, sku)).fetchone()["id"]
    finally:
        conn.close()


def test_import_refuses_a_declaration_that_would_drive_stock_negative(company):
    """The reported end-to-end reproduction, through the real
    /api/import/execute route: import 100, sell 80, re-import declaring 10.

    The delta is -90 against 20 actually on hand. Applied blind it writes
    -70 on hand -- create_sale refuses to oversell, and a spreadsheet must
    not be able to produce the same negative balance behind its back. It is
    also not a coherent instruction: a product cannot have opened with 10
    and then sold 80. Refused, the way adjust_stock refuses, with the
    reason reported back rather than silently floored.
    """
    company_id, client = company
    sku = f"NEG-{uuid.uuid4().hex[:8]}"

    assert _import_products(client, _stock_sheet(sku, 100), _STOCK_HEADERS, _STOCK_MAPPING)["imported"] == 1
    pid = _pid_for_sku(company_id, sku)
    assert _balance(company_id, pid) == 100.0

    sale = client.post("/api/sub/retail/sales", json={
        "items": [{"product_id": pid, "quantity": 80}],
        "payment_method": "cash", "amount_paid": 800,
    })
    assert sale.status_code == 200, sale.get_json()
    assert _balance(company_id, pid) == 20.0

    result = _import_products(client, _stock_sheet(sku, 10), _STOCK_HEADERS, _STOCK_MAPPING)

    # The headline: the balance is untouched, and above all not negative.
    assert _balance(company_id, pid) == 20.0, "a lower re-declaration drove stock negative"
    assert _ledger(company_id, pid) == 20.0
    assert [m for m in _movements(company_id, pid) if m["reference"] == "IMPORT"] == []

    # And the operator is told, by SKU, instead of the figure vanishing.
    errors = result.get("stock_errors") or []
    assert len(errors) == 1, result
    assert errors[0]["sku"] == sku
    assert errors[0]["declared"] == 10.0
    assert errors[0]["on_hand"] == 20.0
    assert errors[0]["would_be"] == -70.0
    assert errors[0]["reason"], errors[0]
    # Never a clean green tick when a declared figure was not applied.
    assert result["status"] == "partial", result

    # The catalogue half of the row still landed -- only the stock was refused.
    assert result["updated"] == 1

    # An unchanged re-declaration is still a no-op, not a second refusal.
    again = _import_products(client, _stock_sheet(sku, 100), _STOCK_HEADERS, _STOCK_MAPPING)
    assert (again.get("stock_errors") or []) == []
    assert _balance(company_id, pid) == 20.0


def test_import_refuses_a_negative_opening_declaration(company):
    """A declared opening of -5 is not an instruction, it is a typo."""
    company_id, client = company
    sku = f"NEGDEC-{uuid.uuid4().hex[:8]}"
    result = _import_products(client, _stock_sheet(sku, -5), _STOCK_HEADERS, _STOCK_MAPPING)
    pid = _pid_for_sku(company_id, sku)
    assert _balance(company_id, pid) == 0.0
    assert _movements(company_id, pid) == []
    errors = result.get("stock_errors") or []
    assert len(errors) == 1 and errors[0]["sku"] == sku, result


def test_import_declaring_zero_is_explicit_and_a_blank_cell_is_not(company):
    """Under delta semantics a 0 is a real instruction ("this product opened
    with nothing"), and the old guard -- `if init_stock and
    float(init_stock) > 0` -- silently discarded it along with genuinely
    blank cells. They mean different things and must behave differently:

        0     -> declare zero; post the correction that gets there.
        blank -> no opinion; leave the balance exactly as it is.
    """
    company_id, client = company
    sku = f"ZERO-{uuid.uuid4().hex[:8]}"

    assert _import_products(client, _stock_sheet(sku, 50), _STOCK_HEADERS, _STOCK_MAPPING)["imported"] == 1
    pid = _pid_for_sku(company_id, sku)
    assert _balance(company_id, pid) == 50.0

    # ── declaring 0 is honoured, with a real signed ledger row ───────────
    zero = _import_products(client, _stock_sheet(sku, 0), _STOCK_HEADERS, _STOCK_MAPPING)
    assert (zero.get("stock_errors") or []) == [], zero
    assert _balance(company_id, pid) == 0.0, "a declared 0 was silently discarded"
    assert _ledger(company_id, pid) == 0.0
    corrections = [m for m in _movements(company_id, pid) if m["reference"] == "IMPORT"]
    assert len(corrections) == 1 and corrections[0]["quantity"] == -50.0
    assert corrections[0]["movement_type"] == "stock_out"

    # ── a BLANK cell is not a declaration of zero, and not a declaration
    #    of anything else either: it leaves the balance alone ─────────────
    client.post(f"/api/sub/retail/products/{pid}/stock-adjust",
                json={"quantity": 12, "reason": "Restocked"})
    assert _balance(company_id, pid) == 12.0
    before = len(_movements(company_id, pid))

    blank = _import_products(client, _stock_sheet(sku, ""), _STOCK_HEADERS, _STOCK_MAPPING)
    assert (blank.get("stock_errors") or []) == [], blank
    assert _balance(company_id, pid) == 12.0, "a blank cell moved stock"
    assert len(_movements(company_id, pid)) == before


def test_import_stock_column_says_what_the_backend_actually_does(company):
    """Operator-facing contract test. The backend applies this column as a
    cumulative opening DECLARATION with a computed delta; the column used to
    be advertised as "Current Stock Qty", which promises a live count. The
    label, the help text and the backend must agree, and both catalogs must
    carry the new strings -- a string missing from a catalog is a defect."""
    _company_id, client = company
    r = client.get("/api/import/schemas")
    assert r.status_code == 200, r.get_json()
    fields = r.get_json()["schemas"]["retail"]["products"]["fields"]
    stock = next(f for f in fields if f["key"] == "initial_stock")

    assert stock["label"] != "Current Stock Qty", "label still promises a live count"
    assert stock["label"] == "Opening Stock Qty"
    help_text = stock.get("help") or ""
    # The three things the operator cannot guess and must not be surprised by.
    assert "opening" in help_text.lower()
    assert "difference" in help_text.lower()          # raising it adds only the delta
    assert "blank" in help_text.lower() and " 0" in help_text   # blank vs zero

    locales = PRODUCT_DIR / "frontend" / "locales"
    for name in ("en.json", "ar.json"):
        catalog = json.loads((locales / name).read_text(encoding="utf-8"))
        for needed in (stock["label"], help_text,
                       "Stock was left unchanged for these products:",
                       "Declared less than has already been sold or moved",
                       "Opening quantity cannot be negative",
                       "currently on hand"):
            assert needed in catalog, f"{needed!r} missing from {name}"
            assert catalog[needed].strip(), f"{needed!r} is blank in {name}"
        if name == "ar.json":
            # An untranslated Arabic entry is the same defect as a missing
            # one -- it just fails silently instead of loudly (i18n.js skips
            # a key whose value equals the key).
            assert catalog[stock["label"]] != stock["label"]
            assert catalog[help_text] != help_text


# ═════════════════════════════════════════════════════════════════════════
# 14. adjust_stock's zero floor is a quantity comparison, not an exact one
# ═════════════════════════════════════════════════════════════════════════

def test_stock_adjustment_floor_tolerates_float_residue(company):
    """quantity_on_hand is REAL and is ACCUMULATED by repeated
    `quantity_on_hand + ?` UPDATEs, so 0.7 then 0.1 lands at
    0.7999999999999999. An exact `on_hand + qty < 0` then refuses "remove
    the 0.8 that is there" as if it were an oversell -- a fractional-unit
    product (kg, litres) can never be zeroed out. The floor must use the
    same tolerance the reconciler and the importer compare with."""
    company_id, client = company
    pid, _sku = _create_product(client, initial_stock=0)

    for step in (0.7, 0.1):
        r = client.post(f"/api/sub/retail/products/{pid}/stock-adjust",
                        json={"quantity": step, "reason": "Weighed in"})
        assert r.status_code == 200, r.get_json()

    on_hand = _balance(company_id, pid)
    assert on_hand < 0.8, f"fixture lost the float residue it exists to exercise ({on_hand!r})"

    r = client.post(f"/api/sub/retail/products/{pid}/stock-adjust",
                    json={"quantity": -0.8, "reason": "Weighed out"})
    assert r.status_code == 200, r.get_json()
    assert abs(_balance(company_id, pid)) < 0.0005
    assert abs(_ledger(company_id, pid) - _balance(company_id, pid)) < 0.0005

    # The floor itself still holds: a real oversell is still refused.
    bad = client.post(f"/api/sub/retail/products/{pid}/stock-adjust",
                      json={"quantity": -1, "reason": "Damaged"})
    assert bad.status_code == 400, bad.get_json()


# ═════════════════════════════════════════════════════════════════════════
# 15. The reconciliation REPORT discloses the whole catalogue -- gate it
# ═════════════════════════════════════════════════════════════════════════

def test_reconciliation_report_requires_a_company_admin(company):
    """GET carried only login + subsystem, while its POST twin required
    company-admin plus a confirmation token. Read-only is not the same as
    harmless: the response is every product name, SKU, branch and quantity
    in the company, unpaginated. A cashier must not be able to export it."""
    company_id, client = company
    pid, _sku = _create_product(client, initial_stock=5)
    _corrupt_balance(company_id, pid, 3)

    cashier = _cashier_client(company_id)
    # The cashier really does have retail access -- this is an authorization
    # failure on the route, not the subsystem gate bouncing them earlier.
    assert cashier.get("/api/sub/retail/products").status_code == 200

    denied = cashier.get("/api/sub/retail/inventory/reconciliation")
    assert denied.status_code == 403, denied.get_json()
    assert "rows" not in (denied.get_json() or {}).get("data", {})

    allowed = client.get("/api/sub/retail/inventory/reconciliation")
    assert allowed.status_code == 200, allowed.get_json()
    assert allowed.get_json()["data"]["drift_count"] == 1


# ═════════════════════════════════════════════════════════════════════════
# 16-20. Phase 7 stage 7c-i's PO-receipt sync guard -- RETRACTED
# ═════════════════════════════════════════════════════════════════════════
#
# docs/launch-readiness/phase7-offline-ux.md, "DO block: receiving a
# purchase order" argued that receive_purchase_order's OWN double-receive
# guard (test 1 above) is per-device-local -- it reads `status` from THIS
# device's own database, and PO status never syncs (multi-device-design.md
# §8 keeps it deliberately device-local) -- so it structurally cannot see a
# receipt already applied by a DIFFERENT device, and added
# `_is_device_behind_on_sync()` to block receipt whenever this device knew
# it was behind.
#
# RETRACTED (2026-08-29): `purchase_orders` is not merely un-synced on its
# `status` column -- the WHOLE TABLE is local-only and never queued to the
# sync outbox at all (sync_service.py's module docstring; retail_api.py's
# accept_reorder_request docstring says the same for every PO this route
# ever creates). A PO exists on exactly ONE device -- the one that created
# it -- so there is no second device to double-receive it on, and the
# cross-device hazard this guard blocked was unreachable. The guard has
# been removed from receive_purchase_order. `_is_device_behind_on_sync()`
# itself is untouched -- it still guards create_sale's stage 7d-iii
# oversell relaxation, a genuine cross-device hazard (balances DO sync).
#
# The five tests below are updated to match: test 16 now asserts the
# CORRECTED behaviour (a behind device CAN receive), tests 17-19 (allowed
# when unconfigured / never-synced / recently-synced) were already true and
# stay true -- receiving was never blocked in those states and still isn't
# -- with their comments no longer describing a cross-device hazard that
# does not exist. Test 20 now asserts a receive on a behind device DOES
# write the stock, the property that actually matters post-retraction.
#
# `_sync_get_active_health` is still monkeypatched directly on the
# `_retail_api` module in each test below, even though receive_purchase_order
# no longer reads it -- this documents, in the test itself, that the route's
# behaviour is now independent of this device's sync health, and keeps the
# fixture available if a future guard ever needs it again.

def _po_for_receipt(client, supplier_name=None):
    """A single pending 10-unit PO for one fresh product, ready to receive."""
    pid, _sku = _create_product(client, initial_stock=0)
    sup = client.post("/api/sub/retail/suppliers", json={
        "name": supplier_name or f"7c-i Supplier {uuid.uuid4().hex[:8]}"})
    assert sup.status_code == 200, sup.get_json()
    po = client.post("/api/sub/retail/purchase-orders", json={
        "supplier_id": sup.get_json()["data"]["id"],
        "items": [{"product_id": pid, "quantity": 10, "unit_cost": 5}],
    })
    assert po.status_code == 200, po.get_json()
    return po.get_json()["data"]["id"], pid


def test_receiving_a_po_is_allowed_when_this_device_is_behind(company, monkeypatch):
    """The corrected behaviour, post-retraction: sync configured AND behind
    the shared threshold no longer blocks receipt. This is the test that
    replaces the old `..._is_blocked_when_this_device_is_behind` -- 7c-i's
    guard assumed a second device could hold a copy of this PO to have
    already received it, but `purchase_orders` never syncs at all (see this
    file's header comment, item 9, and receive_purchase_order's own
    docstring), so no second device can ever be in that state. A behind
    device is exactly as entitled to book in a delivery that physically
    arrived as a fully-synced one is -- this test exists so nobody
    re-adds the block without first making a failing test explain why not."""
    company_id, client = company
    po_id, pid = _po_for_receipt(client)

    monkeypatch.setattr(_retail_api, '_sync_get_active_health', lambda: {
        'configured': True, 'never_synced': False,
        'seconds_since_last_success': _retail_api._SYNC_STALE_THRESHOLD_SECONDS + 1,
    })

    r = client.post(f"/api/sub/retail/purchase-orders/{po_id}/receive", json={})
    assert r.status_code == 200, r.get_json()
    assert _balance(company_id, pid) == 10.0


def test_receiving_a_po_is_allowed_when_sync_is_not_configured(company, monkeypatch):
    """The majority install: sync was never turned on for this device.
    Originally written against 7c-i's now-retracted guard (this file's
    header comment, item 9) to prove its `configured` check didn't brick
    receiving for every single-device shop. Kept as a plain regression now
    that the guard is gone: receipt succeeds in this health state exactly
    as it does in every other, because receive_purchase_order no longer
    reads `_sync_get_active_health` at all -- the mock below is left in
    place only to document that fact for a reader comparing this test
    against tests 18-19."""
    company_id, client = company
    po_id, pid = _po_for_receipt(client)

    monkeypatch.setattr(_retail_api, '_sync_get_active_health', lambda: {
        'configured': False, 'never_synced': False,
        'seconds_since_last_success': _retail_api._SYNC_STALE_THRESHOLD_SECONDS * 100,
    })

    r = client.post(f"/api/sub/retail/purchase-orders/{po_id}/receive", json={})
    assert r.status_code == 200, r.get_json()
    assert _balance(company_id, pid) == 10.0


def test_receiving_a_po_is_allowed_when_the_device_has_never_synced(company, monkeypatch):
    """Configured, but no completed first sync yet. Originally written
    against 7c-i's now-retracted guard (item 9 above) to prove its
    `never_synced` check didn't trip a shop's very first day. Kept as a
    plain regression now that the guard is gone: receipt succeeds in this
    health state exactly as it does in every other, because
    receive_purchase_order no longer reads `_sync_get_active_health` at
    all -- the mock below is left in place only to document that fact for
    a reader comparing this test against tests 17 and 19."""
    company_id, client = company
    po_id, pid = _po_for_receipt(client)

    monkeypatch.setattr(_retail_api, '_sync_get_active_health', lambda: {
        'configured': True, 'never_synced': True,
        'seconds_since_last_success': _retail_api._SYNC_STALE_THRESHOLD_SECONDS * 100,
    })

    r = client.post(f"/api/sub/retail/purchase-orders/{po_id}/receive", json={})
    assert r.status_code == 200, r.get_json()
    assert _balance(company_id, pid) == 10.0


def test_receiving_a_po_is_allowed_when_recently_synced(company, monkeypatch):
    """A healthy till: configured, synced before, comfortably inside the
    threshold. Never depended on the now-retracted 7c-i guard (item 9
    above) to pass -- a healthy till always received normally -- kept
    alongside tests 17-18 so all three health states this route is
    indifferent to post-retraction are pinned in one place."""
    company_id, client = company
    po_id, pid = _po_for_receipt(client)

    monkeypatch.setattr(_retail_api, '_sync_get_active_health', lambda: {
        'configured': True, 'never_synced': False, 'seconds_since_last_success': 5.0,
    })

    r = client.post(f"/api/sub/retail/purchase-orders/{po_id}/receive", json={})
    assert r.status_code == 200, r.get_json()
    assert _balance(company_id, pid) == 10.0


def test_a_receive_on_a_behind_device_writes_the_stock(company, monkeypatch):
    """Replaces the old `..._a_blocked_receive_does_not_change_stock`, which
    asserted the CHECK RAN by requiring a refusal to leave stock untouched
    -- there is no refusal left to check post-retraction (item 9 above), so
    the property that actually matters now is its mirror image: a behind
    device's receive must ACTUALLY WRITE the stock and the ledger row, not
    merely return 200 while silently doing nothing. Reads stock and the
    movement count before and after and requires both to reflect the
    delivery, with sync deliberately reported as badly behind throughout --
    proving receipt is unconditional on this device's sync health, not just
    unblocked in the specific health states tests 17-19 happen to cover."""
    company_id, client = company
    po_id, pid = _po_for_receipt(client)
    before_balance = _balance(company_id, pid)
    before_movements = len(_movements(company_id, pid))
    assert before_balance == 0.0 and before_movements == 0

    monkeypatch.setattr(_retail_api, '_sync_get_active_health', lambda: {
        'configured': True, 'never_synced': False,
        'seconds_since_last_success': _retail_api._SYNC_STALE_THRESHOLD_SECONDS * 10,
    })

    r = client.post(f"/api/sub/retail/purchase-orders/{po_id}/receive", json={})
    assert r.status_code == 200, r.get_json()

    assert _balance(company_id, pid) == 10.0, "a behind device's receive must still update inventory_balances"
    movements = [m for m in _movements(company_id, pid) if m["movement_type"] == "purchase_in"]
    assert len(movements) == 1, "a behind device's receive must still write a purchase_in movement"

    conn = get_retail_conn()
    try:
        status = conn.execute("SELECT status FROM purchase_orders WHERE id=?", (po_id,)).fetchone()["status"]
    finally:
        conn.close()
    assert status == 'received', "a behind device's receive must still flip the PO's own status too"
