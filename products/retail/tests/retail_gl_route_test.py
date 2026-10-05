"""Aura Retail -- GET /api/sub/retail/reports/trial-balance, the one route
that makes schema v38's general ledger (core/retail/general_ledger.py)
reachable from outside Python. Until this route existed, `gl.trial_balance`
was correct (retail_gl_posting_test.py, retail_gl_schema_test.py) and wired
into every money-path route (retail_gl_integration_test.py), but no route
ever returned it -- the ledger was invisible to any user.

THE CENTRAL CLAIM THIS FILE PROVES: the route's `subledger` key -- the AR/AP
tie-back ROADMAP.md's v38 entry actually asks for -- is not merely present,
it is CORRECT in both directions: it says `ties: true` when the GL and the
customers/suppliers subledger genuinely agree, and it says `ties: false`
when they genuinely do not (CLAUDE.md's engineering standard: "prove both
directions of anything that both denies and allows"). A guard that only
ever sees the allow path is half-tested.

HTTP-driven, matching retail_gl_integration_test.py's own convention: every
test that exercises money calls the REAL Flask route (create_sale,
customer_payment), then reads this NEW route back, rather than calling
`core/retail/general_ledger.py` functions directly. Self-contained bootstrap
(no shared conftest.py exists for products/retail/tests/), one company per
test via the `client` fixture.

Run:
    pytest products/retail/tests/retail_gl_route_test.py -q
"""
import os
import shutil
import sqlite3
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

DATA = Path(tempfile.mkdtemp(prefix="aura_gl_route_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR),
                  AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402

seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from commercial_runtime.identity import user_accounts  # noqa: E402
from commercial_runtime.identity.mt_auth import CAPABILITY_DENIED_MESSAGE  # noqa: E402
from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402
from database.schema import get_retail_conn  # noqa: E402
from core.retail import general_ledger as gl  # noqa: E402

API = '/api/sub/retail'


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


@pytest.fixture
def client():
    email = f'glroute-{uuid.uuid4().hex[:8]}@test.local'
    password = 'GlRoutePW1'
    company_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, "
        "require_password_change) VALUES (?,?,?,?,?,?,?,0)",
        (str(uuid.uuid4()), company_id, 'EMP-0001', email, hash_password(password), 'admin', 'active'))
    conn.commit(); conn.close()

    c = app.test_client()
    r = c.post('/api/auth/login', json={'email': email, 'password': password})
    assert r.status_code == 200, r.get_json()
    c.test_company_id = company_id
    return c


def _make_cashier():
    """A real cashier account. `ROLE_CAPABILITIES` (commercial_runtime/
    identity/user_accounts.py) grants a cashier `{CAP_SELL, CAP_REFUND,
    CAP_CASH_CLOSE}` and NOT `CAP_REPORTS` -- this is the account this file
    uses to prove the route's refusal comes from the capability check
    itself, not merely from being unauthenticated or lacking the retail
    subsystem grant."""
    email = f'gl-cashier-{uuid.uuid4().hex[:8]}@test.local'
    password = 'GlCashierPW1'
    company_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, "
        "require_password_change) VALUES (?,?,?,?,?,?,?,0)",
        (user_id, company_id, 'EMP-0001', email, hash_password(password), 'cashier', 'active'))
    # mt_require_subsystem still demands the legacy un-namespaced grant --
    # without this row the request never reaches the capability check at
    # all, and the refusal-came-from-the-capability-check assertion below
    # would pass for the wrong reason.
    conn.execute(
        "INSERT INTO user_permissions (id, user_id, subsystem, access_level) VALUES (?,?,?,?)",
        (str(uuid.uuid4()), user_id, 'retail', 'full'))
    user_accounts.seed_capabilities_for_user(conn, user_id, 'cashier')
    conn.commit(); conn.close()

    c = app.test_client()
    r = c.post('/api/auth/login', json={'email': email, 'password': password})
    assert r.status_code == 200, r.get_json()
    return c


def _seed_product(c, **kw):
    payload = {'name': 'GL Route Widget', 'sku': f'GLR-{uuid.uuid4().hex[:8]}', 'cost_price': 1.0,
               'sell_price': 10.0, 'tax_rate': 0, 'initial_stock': 100}
    payload.update(kw)
    r = c.post(f'{API}/products', json=payload)
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _seed_customer(c):
    r = c.post(f'{API}/customers', json={'name': f'GL Route Customer {uuid.uuid4().hex[:6]}'})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _set_customer_credit(cust_id, mode='unlimited', limit=1000000):
    conn = get_retail_conn()
    conn.execute("UPDATE customers SET credit_mode=?, credit_limit=? WHERE id=?", (mode, limit, cust_id))
    conn.commit(); conn.close()


def _trial_balance(client):
    r = client.get(f'{API}/reports/trial-balance')
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']


def _account(data, code):
    return [a for a in data['accounts'] if a['code'] == code][0]


# ── 1. Basic shape, fresh install ────────────────────────────────────────

def test_route_returns_200_and_balanced_trial_balance_on_fresh_install(client):
    data = _trial_balance(client)
    assert data['is_balanced'] is True
    assert data['total_debit'] == 0.0
    assert data['total_credit'] == 0.0
    # ACCOUNT_AR/ACCOUNT_AP are always present -- ensure_default_chart seeds
    # them even when nothing has ever posted (trial_balance's own LEFT JOIN
    # contract).
    assert _account(data, gl.ACCOUNT_AR)['balance'] == 0.0
    assert _account(data, gl.ACCOUNT_AP)['balance'] == 0.0
    sub = data['subledger']
    assert sub['ar_gl'] == 0.0 and sub['ar_subledger'] == 0.0 and sub['ar_ties'] is True
    assert sub['ap_gl'] == 0.0 and sub['ap_subledger'] == 0.0 and sub['ap_ties'] is True


# ── 2. Credit sale -- AR appears and ties ────────────────────────────────

def test_credit_sale_ar_appears_and_ties_via_route(client):
    cust_id = _seed_customer(client)
    _set_customer_credit(cust_id)
    pid = _seed_product(client, sell_price=50.0, tax_rate=0)
    r = client.post(f'{API}/sales', json={
        'items': [{'product_id': pid, 'quantity': 1}],
        'payment_method': 'credit', 'customer_id': cust_id, 'amount_paid': 20.0,
        'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['data']['balance_due'] == 30.0

    data = _trial_balance(client)
    assert data['is_balanced'] is True
    assert _account(data, gl.ACCOUNT_AR)['balance'] == 30.0
    sub = data['subledger']
    assert sub['ar_gl'] == 30.0
    assert abs(sub['ar_subledger'] - 30.0) < 0.0005
    assert sub['ar_ties'] is True


# ── 3. Customer payment -- AR falls and still ties ───────────────────────

def test_customer_payment_lowers_ar_and_still_ties_via_route(client):
    cust_id = _seed_customer(client)
    _set_customer_credit(cust_id)
    pid = _seed_product(client, sell_price=100.0, tax_rate=0)
    r = client.post(f'{API}/sales', json={
        'items': [{'product_id': pid, 'quantity': 1}],
        'payment_method': 'credit', 'customer_id': cust_id, 'amount_paid': 0,
        'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()

    before = _trial_balance(client)
    assert before['subledger']['ar_gl'] == 100.0
    assert before['subledger']['ar_ties'] is True

    r = client.post(f'{API}/customers/{cust_id}/payments', json={'amount': 40.0, 'method': 'cash'})
    assert r.status_code == 200, r.get_json()

    after = _trial_balance(client)
    assert after['subledger']['ar_gl'] == 60.0
    assert abs(after['subledger']['ar_subledger'] - 60.0) < 0.0005
    assert after['subledger']['ar_ties'] is True


# ── 4. The deny half -- ties must go FALSE when they genuinely do not ────
#
# CLAUDE.md's engineering standard: "a fix that must deny X while still
# allowing Y is half-tested by default, because the deny-half is the
# obvious one." Every test above only ever sees agreement (every real sale/
# payment this codebase can produce keeps the two ledgers in step) -- this
# test introduces a REAL divergence directly against `customers.
# credit_balance`, bypassing every route that keeps the two ledgers
# together, and proves the route's own `ar_ties` notices it.

def test_ar_ties_reports_false_when_subledger_diverges_from_gl(client):
    """THE SIZE OF THE DIVERGENCE IS DELIBERATE: 0.003 JOD (3 mils) is
    bigger than JOD's own half-fil epsilon (0.0005), so the CORRECT epsilon
    must refuse to call this tied -- but smaller than the hardcoded `0.005`
    this exact codebase already shipped once as a real bug (CLAUDE.md's
    round-4 corrections: "~25 money gates compared against a hardcoded
    0.005... never make one of these finer on its own"). This is also the
    vehicle for this file's mutation proofs -- see the task report for both
    directions, quoted."""
    cust_id = _seed_customer(client)
    _set_customer_credit(cust_id)
    pid = _seed_product(client, sell_price=50.0, tax_rate=0)
    r = client.post(f'{API}/sales', json={
        'items': [{'product_id': pid, 'quantity': 1}],
        'payment_method': 'credit', 'customer_id': cust_id, 'amount_paid': 20.0,
        'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()

    tied = _trial_balance(client)
    assert tied['subledger']['ar_ties'] is True

    # Desync the subledger from the GL -- no route does this, which is the
    # point: a real bug that desynced the two ledgers would look exactly
    # like this from the GL's point of view.
    #
    # v44 (plan step 8): was `UPDATE customers SET credit_balance =
    # credit_balance + 0.003`. The subledger is now DERIVED from shop-wide rows,
    # so a poked column is overwritten and would no longer diverge anything.
    # The same 0.003 is now an append-only adjustment row with NO GL journal
    # behind it (exactly what a real desync looks like: a subledger fact the GL
    # never booked), refreshed into the cache as the boot / post-pull sweep
    # does. The 0.003 size and the `ar_ties is False` expectation are
    # unchanged, so the epsilon proof this test carries still bites.
    from core.retail import party_balance
    conn = get_retail_conn()
    cid = conn.execute("SELECT company_id FROM customers WHERE id=?", (cust_id,)).fetchone()[0]
    party_balance.insert_carry(conn, cid, 'customer', cust_id, 0.003)
    party_balance.refresh_cache(conn, cid, 'customers', ids=[cust_id])
    conn.commit(); conn.close()

    diverged = _trial_balance(client)
    sub = diverged['subledger']
    assert abs(sub['ar_gl'] - sub['ar_subledger']) > 0.0005, sub
    assert sub['ar_ties'] is False, (
        f"a genuine {abs(sub['ar_gl'] - sub['ar_subledger'])} JOD divergence "
        f"was reported as tied: {sub!r}")


# ── 5. Capability gate -- refused, and refused BY THE CAPABILITY CHECK ───

def test_route_refused_without_cap_reports_and_refusal_is_from_the_capability_check():
    """A 403 alone cannot distinguish "the capability check ran and
    refused" from "something else refused entirely" (CLAUDE.md's
    engineering standard, failure shape 1). Asserts the CATALOGUED
    `CAPABILITY_DENIED_MESSAGE` -- the one string `mt_require_capability`'s
    own refusal path returns -- matching the exact proof
    retail_report_clock_money_disclosure_test.py already uses for
    `report_summary`."""
    cashier = _make_cashier()
    r = cashier.get(f'{API}/reports/trial-balance')
    assert r.status_code == 403, r.get_json()
    body = r.get_json()
    assert body.get('message') == CAPABILITY_DENIED_MESSAGE, (
        f"refusal did not come from the capability check: {body!r}")


# ── 6. The lazy-column guard -- must not explode ─────────────────────────

def test_subledger_tie_does_not_explode_when_credit_balance_was_never_created():
    """Mirrors retail_gl_schema_test.py's identical guard test for the
    opening-balance backfill migration
    (test_opening_balance_backfill_guards_on_credit_balance_absence) -- a
    hand-built MINIMAL fixture, entirely separate from this file's shared
    Flask app / database, whose customers/suppliers tables genuinely lack
    `credit_balance` (the lazy-column contract `_ensure_credit_schema`'s own
    comment in api/retail_api.py describes: that column is added at runtime,
    not by any versioned migration).

    Deliberately does NOT touch `database.schema.BASE_DIR`/`SUBSYS_DIR` or
    `os.environ['AURA_APP_DATA']` -- both are shared, module-level mutable
    state every other test in this file depends on for `get_retail_conn()`;
    mutating them here would silently break whichever of this file's other
    tests happens to run afterward in the same process. `gl.subledger_tie`
    must not crash against this fixture -- it must report a true "nothing
    to tie back to" (0.0 == 0.0, ties=True), not a 500."""
    tmp = tempfile.mkdtemp(prefix='aura-retail-gl-route-minimal-')
    try:
        db_path = os.path.join(tmp, 'retail_minimal.db')
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.executescript("""
            CREATE TABLE customers (id INTEGER PRIMARY KEY, company_id INTEGER, name TEXT);
            CREATE TABLE suppliers (id INTEGER PRIMARY KEY, company_id INTEGER, name TEXT);
        """)
        conn.commit()

        import database.schema as sch
        # Creates gl_accounts/gl_journals/gl_journal_lines only -- the
        # opening-balance backfill half of this migration returns early
        # the moment it sees `credit_balance` missing from either table,
        # exactly the guard this test exists to prove holds one layer up
        # (subledger_tie), not just at the migration itself.
        sch._migrate_add_general_ledger(conn)
        conn.commit()

        tb = gl.trial_balance(conn, 1)  # must not raise
        sub = gl.subledger_tie(conn, 1, tb)  # must not raise
        assert sub == {
            'ar_gl': 0.0, 'ar_subledger': 0.0, 'ar_ties': True,
            'ap_gl': 0.0, 'ap_subledger': 0.0, 'ap_ties': True,
        }, sub
        conn.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
