"""Aura Retail -- TAX COLLECTED for a period, net of returns.

THE GAP THIS CLOSES. Nothing in this product computed the tax it had
collected. `core/retail/metrics.py` computed revenue, COGS, gross profit,
margin and inventory value; a repo-wide grep for `tax_collected` /
`SUM(tax_amount)` returned ZERO hits in products/retail/backend.

Tax is per-line in the sales CSV export and nobody ever summed it. So a shop
under a sales-tax regime -- which in this product's first market is every
shop, Jordan having mandated e-invoicing since 2024 -- could not answer "how
much tax did I collect this period" without exporting a CSV and summing it by
hand in a spreadsheet. That is the one figure a filing needs every period.

NET OF RETURNS, like every other headline figure in that module. `revenue` is
net (definition #1); a gross tax figure beside a net revenue figure is exactly
the cross-screen contradiction metrics.py exists to kill, and it would also
overstate a tax liability, which is the expensive direction to be wrong in.

THE APPORTIONMENT BASIS, stated because it is a real decision and not an
obvious one. `return_items` carries NO tax column -- only quantity,
unit_price and line_total -- so the tax inside a refund is not stored and has
to be derived. It is derived PROPORTIONALLY ON THE RETURNED VALUE against the
original sale's own immutable figures:

    return_tax = refund_amount * (sales.tax_amount / sales.total)

That is not invented for this metric: it is the SAME rule create_return
already applies to its loyalty reversal ("Settled PROPORTIONALLY on the
returned VALUE ... against the ORIGINAL sale's `total`"), reused rather than
a second basis nobody reconciles with the first. Both inputs are columns on
the sale, both immutable, so the figure is stable however long after the sale
the return happens.

ITS LIMIT, named rather than discovered: for a sale whose lines carry
DIFFERENT tax rates and of which only SOME lines come back, the apportionment
is by value rather than by the specific lines returned, so it can differ from
a line-exact computation. Deriving it line-exactly would mean reading
`products.tax_rate` as it stands now, which is the same stale-rate problem
this module's own KNOWN GAP section already documents for `cost_price` --
a rate changed after the sale would silently restate history. Value-basis on
immutable sale columns is the more defensible of the two.

Run:
    pytest products/retail/tests/retail_tax_collected_test.py -v
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_taxcollected_"))
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
from core.retail import metrics  # noqa: E402

API = '/api/sub/retail'


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


def _make_admin(price=100.0, tax_rate=16.0, stock=50):
    """One product at a real Jordanian-style rate. 16% is the general sales
    tax rate this market actually uses, so the arithmetic below is the
    arithmetic a real shop's filing does."""
    email = f"tax-{uuid.uuid4().hex[:10]}@test.local"
    password = "TaxCollectedPW1"
    company_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, require_password_change) "
        "VALUES (?,?,?,?,?,?,?,0)",
        (str(uuid.uuid4()), company_id, "EMP-0001", email, hash_password(password), "admin", "active"),
    )
    conn.commit()
    conn.close()

    rconn = get_retail_conn()
    rconn.execute("INSERT INTO branches (company_id,name) VALUES (?, 'Main')", (company_id,))
    branch_id = rconn.execute(
        "SELECT id FROM branches WHERE company_id=? ORDER BY id LIMIT 1", (company_id,)).fetchone()[0]

    def _mk(sku, rate):
        rconn.execute(
            "INSERT INTO products (id,company_id,sku,name,cost_price,sell_price,tax_rate) VALUES (?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), company_id, sku, sku, price / 4, price, rate))
        pid = rconn.execute(
            "SELECT id FROM products WHERE company_id=? AND sku=?", (company_id, sku)).fetchone()[0]
        rconn.execute(
            "INSERT INTO inventory_balances (company_id,product_id,branch_id,quantity_on_hand) VALUES (?,?,?,?)",
            (company_id, pid, branch_id, stock))
        return pid

    pid_a = _mk(f'TX-A-{uuid.uuid4().hex[:6]}', tax_rate)
    pid_b = _mk(f'TX-B-{uuid.uuid4().hex[:6]}', tax_rate)
    rconn.commit()
    rconn.close()

    client = app.test_client()
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.get_json()
    client.get(f"{API}/settings/tax")

    import random as _random
    dconn = get_retail_conn()
    for _doc in ('sale', 'return'):
        dconn.execute(
            "INSERT OR REPLACE INTO doc_sequences (company_id,doc_type,last_no) VALUES (?,?,?)",
            (company_id, _doc, _random.randint(1, 5_000_000)))
    dconn.commit()
    dconn.close()
    return client, company_id, branch_id, pid_a, pid_b


def _sell(client, pids):
    r = client.post(f'{API}/sales', json={
        'items': [{'product_id': p, 'quantity': 1} for p in pids],
        'payment_method': 'cash', 'idempotency_key': str(uuid.uuid4()),
    })
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']


def _return(client, sale_id, pids):
    r = client.post(f'{API}/returns', json={
        'sale_id': sale_id, 'items': [{'product_id': p, 'quantity': 1} for p in pids],
        'reason': 'tax test', 'idempotency_key': str(uuid.uuid4()),
    })
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']


def _tax(cid, branch_id=None):
    conn = get_retail_conn()
    try:
        return metrics.tax_collected(
            conn, cid, metrics.period_days(30, metrics.business_now(conn, cid, __import__('datetime').datetime.now())), branch_id, 'JOD')
    finally:
        conn.close()


def _sale_tax(cid):
    """What the sales themselves recorded -- the control every assertion
    below is measured against, read from the same immutable column the
    metric reads."""
    conn = get_retail_conn()
    total = conn.execute(
        "SELECT COALESCE(SUM(tax_amount),0) FROM sales WHERE company_id=?", (cid,)).fetchone()[0]
    conn.close()
    return total


# ── the figure exists at all ──────────────────────────────────────────────────

def test_tax_collected_sums_what_the_sales_recorded():
    """Two 100.00 lines at 16%. Whatever create_sale computed as tax on that
    sale is what the period's tax figure must report -- asserted against the
    sale's own stored `tax_amount`, not against a number retyped here, so the
    test cannot drift from the tax engine's own rounding."""
    client, cid, _bid, pid_a, pid_b = _make_admin(price=100.0, tax_rate=16.0)
    _sell(client, [pid_a, pid_b])

    recorded = _sale_tax(cid)
    assert recorded > 0, "precondition: the sale really did record tax"
    assert _tax(cid) == pytest.approx(recorded, abs=0.0005), (
        f"tax collected must equal what the sales recorded; got {_tax(cid)!r} vs {recorded!r}")


def test_a_zero_rated_shop_collects_no_tax():
    """The control. A shop selling only zero-rated goods must report 0.00 --
    not None, not an error, not a figure conjured from the sale total."""
    client, cid, _bid, pid_a, _pid_b = _make_admin(price=100.0, tax_rate=0.0)
    _sell(client, [pid_a])

    assert _tax(cid) == 0.0, f"got {_tax(cid)!r}"


# ── net of returns, which is the half that is easy to get wrong ───────────────

def test_a_full_return_takes_its_tax_back_out():
    """THE DEFINITION THAT MATTERS. `revenue` is net of returns; a gross tax
    figure printed beside a net revenue figure is the cross-screen
    contradiction metrics.py exists to kill -- and it would overstate a tax
    liability, which is the expensive direction to be wrong in.

    Sell two lines, return both: the shop collected, then handed back, all of
    it. Tax collected for the period is 0."""
    client, cid, _bid, pid_a, pid_b = _make_admin(price=100.0, tax_rate=16.0)
    sale = _sell(client, [pid_a, pid_b])
    assert _tax(cid) > 0, "precondition: tax was collected before the return"

    _return(client, sale['id'], [pid_a, pid_b])

    assert _tax(cid) == pytest.approx(0.0, abs=0.0005), (
        f"a fully returned sale keeps no tax; got {_tax(cid)!r}")


def test_a_partial_return_takes_back_only_its_share():
    """Return one of two equal lines: exactly half the tax comes back out.
    Apportioned on the returned VALUE against the sale's own immutable
    `tax_amount`/`total`, which is the same proportional rule create_return
    already uses for its loyalty reversal."""
    client, cid, _bid, pid_a, pid_b = _make_admin(price=100.0, tax_rate=16.0)
    sale = _sell(client, [pid_a, pid_b])
    full = _sale_tax(cid)

    _return(client, sale['id'], [pid_a])

    assert _tax(cid) == pytest.approx(full / 2, abs=0.0005), (
        f"half the goods came back, so half the tax did; got {_tax(cid)!r} of {full!r}")


# ── it agrees with what the shop is shown elsewhere ───────────────────────────

def test_the_reports_summary_carries_the_figure():
    """It has to reach a screen, not just exist in the module -- this repo
    has shipped complete, gated, tested backends nothing ever called five
    times over. `summary()` is what the Reports KPI cards and the
    emailed/WhatsApped report are both built from."""
    client, cid, _bid, pid_a, pid_b = _make_admin(price=100.0, tax_rate=16.0)
    _sell(client, [pid_a, pid_b])

    conn = get_retail_conn()
    try:
        s = metrics.summary(conn, cid, metrics.period_days(30, metrics.business_now(conn, cid, __import__('datetime').datetime.now())), None, 'JOD')
    finally:
        conn.close()

    assert 'tax_collected' in s, f"summary() keys: {sorted(s)}"
    assert s['tax_collected'] == pytest.approx(_sale_tax(cid), abs=0.0005)


def test_tax_is_branch_scoped_like_every_other_figure():
    """Every metric in this module takes `branch_id`, because the Reports
    branch dropdown moves the charts; a KPI that ignored it would show all
    branches beside charts showing one -- the exact defect the consistency
    suite was written for."""
    client, cid, branch_id, pid_a, pid_b = _make_admin(price=100.0, tax_rate=16.0)
    _sell(client, [pid_a, pid_b])

    assert _tax(cid, branch_id) == pytest.approx(_sale_tax(cid), abs=0.0005)

    conn = get_retail_conn()
    other = conn.execute(
        "INSERT INTO branches (company_id,name) VALUES (?, 'Other') RETURNING id", (cid,)).fetchone()[0]
    conn.commit()
    conn.close()
    assert _tax(cid, other) == 0.0, "a branch that sold nothing collected no tax"
