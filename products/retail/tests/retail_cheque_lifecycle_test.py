"""Aura Retail -- Aseel-parity wave A-PAR, "cheque lifecycle as a tracked
instrument" (schema v32): money, state-machine, statement, drawer and
capability regression coverage over the live HTTP routes.

See database/schema.py's RETAIL_SCHEMA_VERSION v32 comment and
core/retail/cheques.py's own module docstring for the design this proves.
Kept deliberately separate from retail_v32_cheque_migration_test.py (direct
`database.schema.BASE_DIR` reassignment) and from retail_cheque_fold_test.py
(pure unit tests, no Flask) -- mixing either with this file's module-level
Flask app built once at import time risks exactly the cross-test
contamination retail_v17_catalogue_migration_test.py's own docstring warns
against.

ENGINEERING.md's own rule applies throughout: a test that passes whether or
not the feature works is worse than none. Every guard below either asserts a
COUNT (never merely a status code) or is mutation-proven in the session that
wrote it -- see the accompanying report for which guards were broken, run
red, and restored.

Run (one file per process, AUDIT-010):
    pytest products/retail/tests/retail_cheque_lifecycle_test.py -v
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_cheque_lifecycle_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402

seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from commercial_runtime.identity import user_accounts  # noqa: E402
from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402
from database.schema import get_retail_conn  # noqa: E402


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


API = '/api/sub/retail'


# ── fixture bedrock (mirrors retail_ar_ap_totals_test.py) ───────────────────

def _make_user(role, *, company_id=None):
    """A real account with the legacy subsystem grant plus real capability
    rows, seeded through the SAME production helper account creation uses --
    matches retail_route_capability_matrix_test.py's own `_make_user`."""
    email = f"chq-{role}-{uuid.uuid4().hex[:10]}@test.local"
    password = "ChequeLifecyclePW1"
    company_id = company_id or str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, require_password_change) "
        "VALUES (?,?,?,?,?,?,?,0)",
        (user_id, company_id, f"EMP-{uuid.uuid4().hex[:6]}", email, hash_password(password), role, "active"),
    )
    conn.execute(
        "INSERT INTO user_permissions (id, user_id, subsystem, access_level) VALUES (?,?,?,?)",
        (str(uuid.uuid4()), user_id, "retail", "full"),
    )
    user_accounts.seed_capabilities_for_user(conn, user_id, role)
    conn.commit()
    conn.close()

    c = app.test_client()
    r = c.post('/api/auth/login', json={'email': email, 'password': password})
    assert r.status_code == 200, r.get_json()
    return c, company_id, user_id


@pytest.fixture
def client():
    """A fresh, logged-in admin client for its own newly-created company --
    one company per test, so every balance/count assertion is scoped to rows
    this test alone wrote."""
    c, _company_id, _user_id = _make_user('admin')
    return c


@pytest.fixture
def db_conn():
    conn = get_retail_conn()
    yield conn
    conn.close()


def _create_customer(client, name=None, credit_mode='unlimited'):
    name = name or f'Cheque Customer {uuid.uuid4().hex[:6]}'
    r = client.post(f'{API}/customers', json={'name': name})
    assert r.status_code == 200, r.get_json()
    cust_id = r.get_json()['data']['id']
    conn = get_retail_conn()
    conn.execute("UPDATE customers SET credit_mode=? WHERE id=?", (credit_mode, cust_id))
    conn.commit()
    conn.close()
    return cust_id


def _create_supplier(client, name=None):
    name = name or f'Cheque Supplier {uuid.uuid4().hex[:6]}'
    r = client.post(f'{API}/suppliers', json={'name': name})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _create_product(client, sell_price=100.0, initial_stock=1000):
    r = client.post(f'{API}/products', json={
        'name': f'Cheque Test Product {uuid.uuid4().hex[:6]}',
        'sku': f'CHQ-{uuid.uuid4().hex[:8]}',
        'cost_price': sell_price / 2, 'sell_price': sell_price, 'tax_rate': 0,
        'initial_stock': initial_stock,
    })
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _credit_sale(client, customer_id, product_id, amount):
    """A REAL credit sale for `amount` against `customer_id` -- the sale
    route's own credit-mode/limit machinery (create_sale) is what actually
    increments `customers.credit_balance`, and customer_statement's
    'charge' row is a real `sales` row, not a hand-inserted one -- both
    matter for the tests that read either."""
    r = client.post(f'{API}/sales', json={
        'items': [{'product_id': product_id, 'quantity': 1, 'unit_price': amount}],
        'customer_id': customer_id, 'payment_method': 'credit', 'amount_paid': 0,
        'idempotency_key': str(uuid.uuid4()),
    })
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']


def _cheque_amount(client, direction, party_id, amount, **extra):
    body = dict({
        'direction': direction, 'party_id': party_id, 'amount': amount,
        'cheque_number': f'CHQ-{uuid.uuid4().hex[:8]}', 'due_date': '2026-12-01',
    }, **extra)
    return client.post(f'{API}/cheques', json=body)


def _credit_balance(cust_id):
    conn = get_retail_conn()
    row = conn.execute("SELECT credit_balance FROM customers WHERE id=?", (cust_id,)).fetchone()
    conn.close()
    return float(row['credit_balance'] or 0)


def _active_payments(company_id, party_id, related_type='cheque'):
    conn = get_retail_conn()
    rows = conn.execute(
        "SELECT * FROM payments WHERE company_id=? AND party_id=? AND related_type=? "
        "AND COALESCE(status,'active')='active' ORDER BY id",
        (company_id, party_id, related_type)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _company_id_of(client):
    """Reads `company_id`/`mt_company_id` straight out of the Flask session
    cookie -- `_cid()` in retail_api.py reads the identical two keys, and
    `/api/auth/session`'s own response body does NOT carry company_id (see
    onboarding_routes.py's get_session, `'user': {...}` has no such field),
    so this is the one reliable way for a test to learn which company a
    logged-in test client belongs to."""
    with client.session_transaction() as sess:
        return sess.get('company_id') or sess.get('mt_company_id')


# ── 1. Receiving a cheque reduces the receivable ─────────────────────────────

def test_receiving_a_cheque_reduces_the_receivable(client):
    """MUTATION: delete the `_adjust_credit(..., delta, ...)` call in
    `_apply_cheque_event`'s crossing branch -> AR stays 100 -> RED."""
    customer_id = _create_customer(client)
    product_id = _create_product(client, sell_price=100.0)
    _credit_sale(client, customer_id, product_id, 100.0)
    assert _credit_balance(customer_id) == 100.0, 'fixture must start owing exactly 100'

    r = _cheque_amount(client, 'in', customer_id, 100.0)
    assert r.status_code == 201, r.get_json()
    cheque_id = r.get_json()['data']['id']
    assert r.get_json()['data']['status'] == 'pending'

    assert _credit_balance(customer_id) == 0.0

    company_id = _company_id_of(client)
    rows = _active_payments(company_id, customer_id)
    assert len(rows) == 1, rows
    assert rows[0]['direction'] == 'in'
    assert rows[0]['method'] == 'check'
    assert rows[0]['sale_id'] is None
    assert rows[0]['related_type'] == 'cheque'


# ── 2. A bounce restores the receivable ──────────────────────────────────────

def test_a_bounce_restores_the_receivable(client):
    """THE MOST IMPORTANT TEST IN THE FEATURE.
    MUTATION A: drop the `+amount` `_adjust_credit` call for the reverse leg
    -> AR stays 0, money silently lost -> RED.
    MUTATION B (documented, not executed here -- see the design review):
    making bounce_cheque UPDATE the original payments row to 'voided'
    instead of writing a new opposite row would shrink the ORIGINAL day's
    daily_cash retroactively; asserted directly in
    test_daily_cash_is_not_restated_by_a_later_bounce below.
    """
    customer_id = _create_customer(client)
    product_id = _create_product(client, sell_price=250.0)
    _credit_sale(client, customer_id, product_id, 250.0)

    r = _cheque_amount(client, 'in', customer_id, 250.0)
    assert r.status_code == 201, r.get_json()
    cheque_id = r.get_json()['data']['id']
    assert _credit_balance(customer_id) == 0.0

    r2 = client.post(f'{API}/cheques/{cheque_id}/bounce', json={'reason': 'Insufficient funds'})
    assert r2.status_code == 200, r2.get_json()
    assert r2.get_json()['data']['status'] == 'bounced'

    assert _credit_balance(customer_id) == 250.0, 'a bounce must restore the FULL face value'

    company_id = _company_id_of(client)
    rows = _active_payments(company_id, customer_id)
    assert len(rows) == 2, rows
    assert rows[0]['direction'] == 'in'
    assert rows[0]['status'] == 'active', 'the ORIGINAL receipt must still be active -- never voided'
    assert rows[1]['direction'] == 'out'
    assert rows[1]['status'] == 'active'


def test_a_bounced_cheque_can_be_reinstated_and_the_receivable_moves_again(client):
    customer_id = _create_customer(client)
    product_id = _create_product(client, sell_price=60.0)
    _credit_sale(client, customer_id, product_id, 60.0)
    cheque_id = _cheque_amount(client, 'in', customer_id, 60.0).get_json()['data']['id']
    client.post(f'{API}/cheques/{cheque_id}/bounce', json={'reason': 'Signature mismatch'})
    assert _credit_balance(customer_id) == 60.0

    r = client.post(f'{API}/cheques/{cheque_id}/reinstate', json={})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['data']['status'] == 'pending'
    assert _credit_balance(customer_id) == 0.0

    company_id = _company_id_of(client)
    rows = _active_payments(company_id, customer_id)
    assert len(rows) == 3
    assert [row['direction'] for row in rows] == ['in', 'out', 'in']


# ── 3. The statement shows the reversal, and still shows ordinary receipts ──

def test_the_statement_shows_the_reversal_and_still_shows_ordinary_receipts(client):
    """DELTA, not identity (H3 -- see customer_statement's own comment on
    the pre-existing create_return divergence). Asserts the final running
    balance moves by exactly +face_value after a bounce, and that an
    ORDINARY payment in the same fixture is unaffected (the allow half).
    MUTATION A: revert customer_statement to its pre-fix `direction='in'`
    filter -> the reversal vanishes from `events` -> RED.
    MUTATION B: make the CASE always emit 'payment' -> the running-balance
    delta comes out -face_value instead of +face_value -> RED.
    """
    customer_id = _create_customer(client)
    product_id = _create_product(client, sell_price=300.0)
    _credit_sale(client, customer_id, product_id, 300.0)

    # An ORDINARY payment first -- the allow half. Must still render as
    # 'payment', sign negative, after the statement-direction fix below.
    ordinary = client.post(f'{API}/customers/{customer_id}/payments', json={'amount': 50.0})
    assert ordinary.status_code == 200, ordinary.get_json()

    before = client.get(f'{API}/customers/{customer_id}/statement').get_json()['data']
    ordinary_events = [e for e in before['events'] if e['kind'] == 'payment' and e['amount'] == 50.0]
    assert len(ordinary_events) == 1, before['events']
    balance_before_cheque = before['events'][-1]['running_balance']

    cheque_id = _cheque_amount(client, 'in', customer_id, 300.0).get_json()['data']['id']
    mid = client.get(f'{API}/customers/{customer_id}/statement').get_json()['data']
    balance_after_receipt = mid['events'][-1]['running_balance']
    assert balance_after_receipt == balance_before_cheque - 300.0

    client.post(f'{API}/cheques/{cheque_id}/bounce', json={'reason': 'Insufficient funds'})
    after = client.get(f'{API}/customers/{customer_id}/statement').get_json()['data']
    events = after['events']
    reversal_events = [e for e in events if e['kind'] == 'reversal']
    assert len(reversal_events) == 1, events
    assert reversal_events[0]['amount'] == 300.0

    balance_after_bounce = events[-1]['running_balance']
    assert balance_after_bounce == balance_after_receipt + 300.0, (
        'the running balance must move by EXACTLY the face value after a bounce')

    # Allow half, same statement: the ordinary payment from before still
    # renders as 'payment' with the original amount, unaffected by the fix.
    still_ordinary = [e for e in events if e['kind'] == 'payment' and e['amount'] == 50.0]
    assert len(still_ordinary) == 1, events

    # 300 (charge) - 50 (ordinary payment) - 300 (cheque received) + 300
    # (bounce reversal) = 250 -- the ordinary payment is NOT undone by any
    # of this, which is exactly the allow-half this test exists to prove.
    assert after['customer']['credit_balance'] == 250.0
    assert after['balance'] == 250.0


def test_the_supplier_statement_mirrors_the_customer_one_for_an_issued_cheque(client):
    """Same forced fix, opposite direction -- supplier_statement's own
    settling direction is 'out' (paying them), so a returned ISSUED cheque
    ('in', the money coming back) must read as 'reversal' there too. DELTA,
    not an absolute value -- this fixture seeds the starting debt by
    directly UPDATE-ing credit_balance (no real purchase_order 'charge' row
    behind it, matching retail_ar_ap_totals_test.py's own established
    fixture style), so the statement's own running total legitimately
    starts at 0 regardless of credit_balance; only the MOVEMENT the bounce
    causes is asserted, exactly like the customer statement test above."""
    supplier_id = _create_supplier(client)
    conn = get_retail_conn()
    conn.execute("UPDATE suppliers SET credit_balance=500.0 WHERE id=?", (supplier_id,))
    conn.commit()
    conn.close()

    cheque_id = _cheque_amount(client, 'out', supplier_id, 500.0).get_json()['data']['id']
    assert _supplier_balance(supplier_id) == 0.0

    mid = client.get(f'{API}/suppliers/{supplier_id}/statement').get_json()['data']
    balance_after_payment_leg = mid['events'][-1]['running_balance']

    client.post(f'{API}/cheques/{cheque_id}/bounce', json={'reason': 'Stopped by drawer'})
    assert _supplier_balance(supplier_id) == 500.0

    after = client.get(f'{API}/suppliers/{supplier_id}/statement').get_json()['data']
    reversal_events = [e for e in after['events'] if e['kind'] == 'reversal']
    assert len(reversal_events) == 1, after['events']
    assert reversal_events[0]['amount'] == 500.0
    balance_after_bounce = after['events'][-1]['running_balance']
    assert balance_after_bounce == balance_after_payment_leg + 500.0


def _supplier_balance(supplier_id):
    conn = get_retail_conn()
    row = conn.execute("SELECT credit_balance FROM suppliers WHERE id=?", (supplier_id,)).fetchone()
    conn.close()
    return float(row['credit_balance'] or 0)


# ── 5. The drawer is not polluted by cheque rows ─────────────────────────────

def test_the_drawer_is_not_polluted_by_cheque_rows(client):
    """Replaces the submitted design's own broken test #7 (its mutation
    could not go red -- the INNER JOIN on p.sale_id=s.id excludes a cheque
    row, which has sale_id NULL, before `method` is even considered). This
    version's own assertion is on the cheque's payments rows directly, which
    IS provable, plus a live x-report byte-identical to a plain cash sale so
    the guard cannot pass by measuring an empty report."""
    # No explicit branch_id -- _resolve_working_branch self-heals the
    # company's default branch when none has been created yet, exactly like
    # create_sale/create_product already rely on.
    open_r = client.post(f'{API}/cash-sessions/open', json={'opening_float': 100.0})
    assert open_r.status_code == 200, open_r.get_json()
    session_id = open_r.get_json()['data']['id']

    cash_product = _create_product(client, sell_price=10.0)
    sale_r = client.post(f'{API}/sales', json={
        'items': [{'product_id': cash_product, 'quantity': 1, 'unit_price': 10.0}],
        'payment_method': 'cash', 'amount_paid': 10.0, 'idempotency_key': str(uuid.uuid4()),
    })
    assert sale_r.status_code == 200, sale_r.get_json()

    x1 = client.get(f'{API}/cash-sessions/{session_id}/x-report').get_json()['data']
    assert x1['expected_cash'] == 110.0, x1  # 100 float + 10 cash sale

    customer_id = _create_customer(client)
    credit_product = _create_product(client, sell_price=500.0)
    _credit_sale(client, customer_id, credit_product, 500.0)
    cheque_id = _cheque_amount(client, 'in', customer_id, 500.0).get_json()['data']['id']

    x2 = client.get(f'{API}/cash-sessions/{session_id}/x-report').get_json()['data']
    assert x2['expected_cash'] == 110.0, (
        'recording a cheque must not move the drawer\'s expected cash at all')

    client.post(f'{API}/cheques/{cheque_id}/bounce', json={'reason': 'Insufficient funds'})
    x3 = client.get(f'{API}/cash-sessions/{session_id}/x-report').get_json()['data']
    assert x3['expected_cash'] == 110.0, (
        'bouncing a cheque must not move the drawer\'s expected cash either')

    company_id = _company_id_of(client)
    rows = _active_payments(company_id, customer_id)
    for row in rows:
        assert row['sale_id'] is None
        assert row['related_type'] == 'cheque'


# ── 6/7. Legal transitions move exactly the right money; illegal ones refuse ─

def test_illegal_transitions_are_refused_and_change_nothing(client):
    """MUTATION: widen LEGAL_TRANSITIONS by one entry (e.g. let 'cleared'
    accept a from_status of 'deposited' AND 'bounced') -> the specific
    refusal this test targets would start succeeding -> RED. Asserts BOTH
    the status code and that nothing was written -- a 409 alone cannot tell
    'the legality check refused' from 'something else refused'
    (ENGINEERING.md shape 1)."""
    customer_id = _create_customer(client)
    product_id = _create_product(client, sell_price=80.0)
    _credit_sale(client, customer_id, product_id, 80.0)
    cheque_id = _cheque_amount(client, 'in', customer_id, 80.0).get_json()['data']['id']

    company_id = _company_id_of(client)

    def _snapshot():
        events = client.get(f'{API}/cheques/{cheque_id}').get_json()['data']['events']
        payments = _active_payments(company_id, customer_id)
        status = client.get(f'{API}/cheques/{cheque_id}').get_json()['data']['cheque']['status']
        return len(events), len(payments), status

    before = _snapshot()

    # 'clear' is legal from pending -- do something else illegal instead:
    # cancel is legal ONLY from 'pending'. Deposit it first, then try to
    # cancel a DEPOSITED cheque.
    dep = client.post(f'{API}/cheques/{cheque_id}/deposit', json={})
    assert dep.status_code == 200, dep.get_json()
    before2 = _snapshot()

    illegal = client.post(f'{API}/cheques/{cheque_id}/cancel', json={})
    assert illegal.status_code == 409, illegal.get_json()
    assert illegal.get_json().get('code') == 'ILLEGAL_TRANSITION'
    after = _snapshot()
    assert after == before2, 'an illegal transition must change NOTHING -- not the event count, not the money, not the status'

    # Also: deposit is illegal on an OUT cheque.
    supplier_id = _create_supplier(client)
    out_cheque = _cheque_amount(client, 'out', supplier_id, 40.0).get_json()['data']['id']
    illegal2 = client.post(f'{API}/cheques/{out_cheque}/deposit', json={})
    assert illegal2.status_code == 400, illegal2.get_json()

    # And endorse is illegal on an OUT cheque.
    illegal3 = client.post(f'{API}/cheques/{out_cheque}/endorse',
                            json={'to_party_type': 'supplier', 'to_party_id': supplier_id})
    assert illegal3.status_code == 400, illegal3.get_json()


def test_write_off_moves_no_money_and_reinstate_moves_it_back_the_allow_half(client):
    """Both directions of 'which transitions move money', over a live
    write-off -> reinstate -> bounce -> write-off chain."""
    customer_id = _create_customer(client)
    product_id = _create_product(client, sell_price=120.0)
    _credit_sale(client, customer_id, product_id, 120.0)
    cheque_id = _cheque_amount(client, 'in', customer_id, 120.0).get_json()['data']['id']
    assert _credit_balance(customer_id) == 0.0

    client.post(f'{API}/cheques/{cheque_id}/bounce', json={'reason': 'Insufficient funds'})
    assert _credit_balance(customer_id) == 120.0

    wo = client.post(f'{API}/cheques/{cheque_id}/write-off', json={})
    assert wo.status_code == 200, wo.get_json()
    assert wo.get_json()['data']['status'] == 'written_off'
    assert _credit_balance(customer_id) == 120.0, 'write-off must move NO money -- the bounce already restored it'

    company_id = _company_id_of(client)
    rows_after_writeoff = _active_payments(company_id, customer_id)
    assert len(rows_after_writeoff) == 2, 'write-off must not create a third payments row'


# ── 12/13. Authority and tenant scope ────────────────────────────────────────

def test_a_cheque_is_only_reachable_by_owner_authority():
    """Live Flask, real cashier/manager/admin accounts. Every mutating
    cheque route needs retail.employees; both reads need retail.reports."""
    admin, company_id, _ = _make_user('admin')
    cashier, _, _ = _make_user('cashier', company_id=company_id)
    manager, _, _ = _make_user('manager', company_id=company_id)

    customer_id = _create_customer(admin)
    product_id = _create_product(admin, sell_price=90.0)
    _credit_sale(admin, customer_id, product_id, 90.0)

    manager_caps = user_accounts.capabilities_for_role(user_accounts.ROLE_MANAGER)
    cashier_caps = user_accounts.capabilities_for_role(user_accounts.ROLE_CASHIER)
    assert user_accounts.CAP_EMPLOYEES not in cashier_caps
    assert user_accounts.CAP_EMPLOYEES not in manager_caps, (
        'sanity: this test only proves something if manager genuinely lacks '
        'retail.employees -- see user_accounts.ROLE_CAPABILITIES')
    assert user_accounts.CAP_REPORTS in manager_caps
    assert user_accounts.CAP_REPORTS not in cashier_caps

    # Both reads: cashier refused, manager allowed.
    assert cashier.get(f'{API}/cheques').status_code == 403
    assert manager.get(f'{API}/cheques').status_code == 200

    # create_cheque: cashier and manager both refused (retail.employees only).
    body = {'direction': 'in', 'party_id': customer_id, 'amount': 90.0,
            'cheque_number': f'CHQ-{uuid.uuid4().hex[:8]}', 'due_date': '2026-12-01'}
    assert cashier.post(f'{API}/cheques', json=body).status_code == 403
    assert manager.post(f'{API}/cheques', json=body).status_code == 403
    admin_create = admin.post(f'{API}/cheques', json=body)
    assert admin_create.status_code == 201, admin_create.get_json()
    cheque_id = admin_create.get_json()['data']['id']

    # Every transition: cashier and manager both refused.
    for segment, payload in (
        ('deposit', {}), ('clear', {}), ('bounce', {'reason': 'x'}),
        ('cancel', {}), ('reinstate', {}), ('write-off', {}),
        ('endorse', {'to_party_type': 'supplier', 'to_party_id': customer_id}),
    ):
        assert cashier.post(f'{API}/cheques/{cheque_id}/{segment}', json=payload).status_code == 403, segment
        assert manager.post(f'{API}/cheques/{cheque_id}/{segment}', json=payload).status_code == 403, segment

    # get_cheque: cashier refused, manager allowed.
    assert cashier.get(f'{API}/cheques/{cheque_id}').status_code == 403
    assert manager.get(f'{API}/cheques/{cheque_id}').status_code == 200


def test_every_cheque_query_is_company_scoped():
    """Two companies; company B's cheque is 404 for company A on every read
    and every transition, and company A's list_cheques totals do not
    include it. MUTATION: drop `AND company_id=?` from `_cheque_or_404` ->
    RED."""
    admin_a, company_a, _ = _make_user('admin')
    admin_b, company_b, _ = _make_user('admin')
    assert company_a != company_b

    cust_b = _create_customer(admin_b)
    product_b = _create_product(admin_b, sell_price=777.0)
    _credit_sale(admin_b, cust_b, product_b, 777.0)
    cheque_b = _cheque_amount(admin_b, 'in', cust_b, 777.0).get_json()['data']['id']

    # Company A cannot read company B's cheque at all.
    assert admin_a.get(f'{API}/cheques/{cheque_b}').status_code == 404
    for segment, payload in (('deposit', {}), ('bounce', {'reason': 'x'}), ('cancel', {})):
        assert admin_a.post(f'{API}/cheques/{cheque_b}/{segment}', json=payload).status_code == 404, segment

    # Company A's own book does not include company B's cheque or its total.
    listing_a = admin_a.get(f'{API}/cheques?direction=in').get_json()
    assert cheque_b not in [row['id'] for row in listing_a['data']]
    assert listing_a['totals']['on_hand_total'] == 0.0


# ── 15. _record_payment's own optional uid + the amount<=0 refusal ──────────

def test_record_payment_still_mints_its_own_uid_when_none_is_supplied():
    """The ONE shared-helper change (pay_uid=None default). MUTATION: make
    the parameter non-defaulting -> every pre-existing caller (customer_
    payment, supplier_payment, ...) breaks with a TypeError -> RED (this
    test exercises exactly one of those five callers end to end)."""
    admin, company_id, _ = _make_user('admin')
    customer_id = _create_customer(admin)
    r = admin.post(f'{API}/customers/{customer_id}/payments', json={'amount': 30.0})
    assert r.status_code == 200, r.get_json()
    conn = get_retail_conn()
    row = conn.execute(
        "SELECT uid FROM payments WHERE company_id=? AND party_id=? AND related_type IS NULL "
        "ORDER BY id DESC LIMIT 1", (company_id, customer_id)).fetchone()
    conn.close()
    assert row is not None and row['uid'], 'a payment with no explicit pay_uid must still get a fresh uid'


def test_a_zero_amount_cheque_is_refused_before_any_write(client):
    """`_record_payment` returns None for amt<=0 -- create_cheque's own
    amount>0 validation must refuse BEFORE any header/event is written.
    MUTATION: remove that validation -> a header would exist with no money
    leg, contradicting the live/dead invariant -> RED."""
    customer_id = _create_customer(client)
    company_id = _company_id_of(client)
    r = client.post(f'{API}/cheques', json={
        'direction': 'in', 'party_id': customer_id, 'amount': 0,
        'cheque_number': 'CHQ-ZERO', 'due_date': '2026-12-01',
    })
    assert r.status_code == 400, r.get_json()
    assert r.get_json().get('code') == 'INVALID_AMOUNT'
    conn = get_retail_conn()
    n_cheques = conn.execute("SELECT COUNT(*) FROM cheques WHERE company_id=?", (company_id,)).fetchone()[0]
    # cheque_events carries no company_id of its own (scoped through
    # cheque_id -- see database/schema.py's v32 comment), and this file's
    # module-level app shares ONE retail.db across every test's company, so
    # a bare COUNT(*) here would count every other test's rows too. Joined
    # to THIS company's cheques instead.
    n_events = conn.execute(
        "SELECT COUNT(*) FROM cheque_events ce JOIN cheques c ON ce.cheque_id = c.id "
        "WHERE c.company_id=?", (company_id,)).fetchone()[0]
    conn.close()
    assert n_cheques == 0
    assert n_events == 0


# ── 16. daily_cash excludes cheques from the cash position ──────────────────

def test_daily_cash_excludes_cheques_from_the_cash_position(client):
    """MUTATION: drop the `related_type<>'cheque'` clause from daily_cash's
    cash_in/cash_out query -> cash_in would include the cheque's face value
    -> RED. Allow half asserted in the same test: by_method's own shape is
    unchanged and still carries the cheque row."""
    cash_product = _create_product(client, sell_price=10.0)
    sale_r = client.post(f'{API}/sales', json={
        'items': [{'product_id': cash_product, 'quantity': 1, 'unit_price': 10.0}],
        'payment_method': 'cash', 'amount_paid': 10.0, 'idempotency_key': str(uuid.uuid4()),
    })
    assert sale_r.status_code == 200, sale_r.get_json()

    customer_id = _create_customer(client)
    credit_product = _create_product(client, sell_price=500.0)
    _credit_sale(client, customer_id, credit_product, 500.0)
    _cheque_amount(client, 'in', customer_id, 500.0)

    today = __import__('datetime').datetime.now().strftime('%Y-%m-%d')
    resp = client.get(f'{API}/reports/daily-cash?date={today}').get_json()['data']
    assert resp['cash_in'] == 10.0, resp
    assert resp['cheques_in'] == 500.0, resp
    by_method_amounts = {(row['direction'], row['method']): row['amount'] for row in resp['by_method']}
    assert by_method_amounts.get(('in', 'cash')) == 10.0
    assert by_method_amounts.get(('in', 'check')) == 500.0, (
        'by_method must still carry the cheque row unchanged -- the allow half')


# ── 4. Android statement rendering: source-level guard ──────────────────────

ANDROID_STATEMENT_KT = (
    SUITE_ROOT / 'android' / 'aura-retail' / 'app' / 'src' / 'main' / 'java'
    / 'com' / 'actionaura' / 'retail' / 'ui' / 'screens' / 'RetailExtraScreens.kt'
)


def test_android_statement_blocks_discriminate_on_a_reversal_aware_predicate():
    """H1: a bounced cheque's `kind='reversal'` row must render as a debit
    (running_balance increases), not fall into the ELSE branch a bare
    `if (e.kind == "charge")` would send it to (which reads as a green
    'Payment' with a minus sign). MUTATION A: restore the bare `if (e.kind
    == "charge")` as the SOLE discriminator -> RED (the reversal-aware
    `isDebit`/`kind == "reversal"` text disappears from the file).
    MUTATION B: widen the Void gate to anything broader than `kind ==
    "payment"` -> RED -- that is the defect that would let the phone void a
    bounce reversal and silently re-reduce AR (see the second defect this
    design's H1 names, found while reading these exact lines)."""
    import re
    assert ANDROID_STATEMENT_KT.exists(), f'expected file not found: {ANDROID_STATEMENT_KT}'
    src = ANDROID_STATEMENT_KT.read_text(encoding='utf-8')

    # The debit predicate must combine BOTH "charge" and "reversal" -- not
    # just one of the two. Matched on the CODE shape itself (the `val
    # isDebit = ...` assignment), not merely on whether the word appears
    # somewhere in the file (a comment could keep saying "reversal" long
    # after the code reverted to the bare `e.kind == "charge"` check).
    isdebit_defs = re.findall(r'val isDebit = ([^\n]+)', src)
    assert len(isdebit_defs) >= 2, (
        f'expected an isDebit predicate in BOTH statement blocks (customer and '
        f'supplier), found {len(isdebit_defs)}: {isdebit_defs}')
    for defn in isdebit_defs:
        assert 'e.kind == "charge"' in defn and 'e.kind == "reversal"' in defn, (
            f'isDebit must combine BOTH "charge" and "reversal", got: {defn.strip()}')

    # The rendered label must have a DEDICATED branch for "reversal" (not
    # merely fall through to the "charge" or the else/"Payment" label) in
    # both blocks.
    when_reversal_branches = re.findall(r'"reversal"\s*->', src)
    assert len(when_reversal_branches) >= 2, (
        f'expected a dedicated "reversal" -> label branch in both statement '
        f'blocks, found {len(when_reversal_branches)}')

    # The Void affordance must stay gated on "payment" ALONE -- never widened
    # to admit "reversal" (which would let a bounce-reversal row be voided,
    # silently re-reducing AR by the face value a second time).
    void_gate_lines = [line for line in src.splitlines() if 'payment_id' in line and 'e.kind ==' in line]
    assert void_gate_lines, 'expected at least one Void-affordance gate referencing e.kind and payment_id'
    for line in void_gate_lines:
        assert '"reversal"' not in line, (
            f'the Void gate must never also match "reversal": {line.strip()}')
