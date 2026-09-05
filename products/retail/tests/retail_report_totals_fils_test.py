"""
Aura Retail -- the last four report figures (plus the accounting export) that
were still quantized to 0.01 after the 2026-09-03 money-precision waves.

`total_receivable`, `total_payable`, `daily_cash`'s `cash_in`/`cash_out`/`net`,
and `aging_report`'s buckets are the numbers a JOD shop's owner actually reads
to know what the business is owed, what it owes, and what is in the drawer --
not a line-item detail like a single sale total, which was already fixed. A
2dp rounding on any of these silently drops the dinar's third decimal (the
fils) by up to 5 fils per figure, and because these are SUMS, the error does
not cancel -- it just moves. `_export_money` (the CSV accounting export) had
the identical defect: a hardcoded `tax_engine._money(Decimal(...))` with no
quantum argument, so an export could disagree with the very ledger it claims
to mirror. This file pins both fixes against the default JOD install.

Run:
    pytest products/retail/tests/retail_report_totals_fils_test.py -v
"""
import os
import shutil
import sys
import tempfile
from datetime import datetime
from decimal import Decimal
from pathlib import Path

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_report_fils_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)
os.environ.pop("AURA_RETAIL_DEMO_MODE", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402
seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from api.retail_api import _export_money  # noqa: E402
from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from database.schema import get_retail_conn  # noqa: E402

API = '/api/sub/retail'
OWNER_EMAIL = 'report-fils-owner@test.local'
OWNER_PASSWORD = 'ReportFilsPW1'

#: The exact fils-bearing figure a 2dp regression cannot reproduce:
#: ROUND_HALF_UP at the second decimal turns 12.345 into 12.35.
FILS_AMOUNT = 12.345


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


# ── Fixtures ─────────────────────────────────────────────────────────────────

def _owner():
    """The one admin this install allows -- same shape as
    retail_employee_management_test.py's own `_owner()`: `create-admin`
    the first time, login every time after."""
    client = app.test_client()
    r = client.post('/api/onboarding/create-admin', json={
        'name': 'Owner', 'email': OWNER_EMAIL, 'password': OWNER_PASSWORD,
    })
    if r.status_code == 409:
        r = client.post('/api/auth/login', json={'email': OWNER_EMAIL, 'password': OWNER_PASSWORD})
    assert r.status_code == 200, r.get_json()
    return client


def _company_id():
    conn = registry_conn()
    try:
        return conn.execute("SELECT company_id FROM users WHERE email=?", (OWNER_EMAIL,)).fetchone()['company_id']
    finally:
        conn.close()


# Bootstrap once at import time, matching retail_accounting_export_test.py's
# `_new_shop()` convention: one GET against a credit-schema route BEFORE any
# direct-SQL insert touches `customers.credit_balance` / `suppliers.
# credit_balance` / `payments.direction`, all of which `_ensure_credit_
# schema` (api/retail_api.py) adds lazily rather than in the base CREATE
# TABLE -- a raw INSERT against those columns on a brand-new install raises
# "no such column" without this.
_bootstrap_client = _owner()
COMPANY_ID = _company_id()
_bootstrap = _bootstrap_client.get(f'{API}/customers/receivables')
assert _bootstrap.status_code == 200, _bootstrap.get_json()

TODAY = datetime.now().strftime('%Y-%m-%d')


def _seed_fixture_rows():
    """One customer, one supplier, one payment -- each carrying the same
    fils-bearing 12.345 -- direct SQL, matching every sibling accounting/
    report test file's convention (there is no "set a customer's opening
    balance" route to seed these figures through). `status='active'` on the
    payment, not 'success': `daily_cash`'s own query filters on
    `COALESCE(status,'active')='active'`, unlike the export routes' seed
    helpers in retail_accounting_export_test.py, which test a different
    surface with a different filter."""
    conn = get_retail_conn()
    conn.execute(
        "INSERT INTO customers (company_id, name, credit_balance) VALUES (?,?,?)",
        (COMPANY_ID, 'Fils Test Customer', FILS_AMOUNT),
    )
    conn.execute(
        "INSERT INTO suppliers (company_id, name, credit_balance) VALUES (?,?,?)",
        (COMPANY_ID, 'Fils Test Supplier', FILS_AMOUNT),
    )
    conn.execute(
        "INSERT INTO payments (company_id, method, amount, status, direction, created_at) "
        "VALUES (?,?,?,?,?,?)",
        (COMPANY_ID, 'cash', FILS_AMOUNT, 'active', 'in', f'{TODAY} 09:00:00'),
    )
    conn.commit()
    conn.close()


_seed_fixture_rows()


# ── The precondition that makes every test below meaningful ────────────────

def test_shop_currency_defaults_to_jod_so_the_fils_figures_below_are_real():
    """A 2dp regression is invisible on a USD/EUR shop -- those currencies
    round to cents anyway. This whole file only proves anything because a
    fresh install with no `base_currency` row defaults to JOD
    (`tax_engine.DEFAULT_BASE_CURRENCY`), which has three."""
    owner = _owner()
    r = owner.get(f'{API}/settings/tax')
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['data']['base_currency'] == 'JOD'


# ── The four report figures ─────────────────────────────────────────────────

def test_total_receivable_keeps_fils_on_a_jod_shop():
    owner = _owner()
    r = owner.get(f'{API}/customers/receivables')
    assert r.status_code == 200, r.get_json()
    # The pre-fix `_money(total)` with no currency argument would answer
    # 12.35 here -- ROUND_HALF_UP at the second decimal silently drops the
    # third for a JOD shop's own "what am I owed" figure.
    assert r.get_json()['total_receivable'] == FILS_AMOUNT


def test_total_payable_keeps_fils_on_a_jod_shop():
    owner = _owner()
    r = owner.get(f'{API}/suppliers/payables')
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['total_payable'] == FILS_AMOUNT


def test_daily_cash_keeps_fils_on_a_jod_shop():
    owner = _owner()
    r = owner.get(f'{API}/reports/daily-cash?date={TODAY}')
    assert r.status_code == 200, r.get_json()
    data = r.get_json()['data']
    assert data['cash_in'] == FILS_AMOUNT
    assert data['net'] == FILS_AMOUNT


def test_aging_buckets_keep_fils_on_a_jod_shop():
    """Which bucket the seeded customer lands in depends on `sales.
    created_at`, which this fixture never wrote (no sale backs the balance),
    so it always lands in 'current' -- the sum across buckets is the
    currency-stable assertion, not any one bucket."""
    owner = _owner()
    r = owner.get(f'{API}/reports/aging')
    assert r.status_code == 200, r.get_json()
    data = r.get_json()['data']
    assert sum(data.values()) == FILS_AMOUNT


# ── The accounting export's own rounding ────────────────────────────────────

def test_export_money_uses_the_currency_quantum():
    """`_export_money` now takes the same `currency` argument every other
    converted call site in this wave does. JOD keeps the third decimal; USD
    (and the no-currency default, for every pre-existing caller) still
    rounds to two -- the identical two-way guarantee `_money` itself
    carries."""
    assert _export_money(Decimal('12.345'), 'JOD') == 12.345
    assert _export_money(Decimal('12.345'), 'USD') == 12.35
    assert _export_money(None, 'JOD') == ''
    # No currency at all -- every call site this wave did NOT touch -- is
    # byte-for-byte the old behaviour.
    assert _export_money(Decimal('12.345')) == 12.35
