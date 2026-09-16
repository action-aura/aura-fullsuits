"""Aura Retail -- Aseel-parity wave A-PAR, "quotations and sales orders as
real documents" (schema v33): lifecycle, pricing, capability and scoping
regression coverage over the live HTTP routes.

See database/schema.py's RETAIL_SCHEMA_VERSION v33 comment and
`_migrate_add_sales_quotations`'s own docstring for the design this proves,
and retail_api.py's QUOTATIONS AND SALES ORDERS section banner for the route
implementation.

Kept deliberately separate from retail_v33_quotation_migration_test.py
(direct `database.schema.BASE_DIR` reassignment, no Flask app) for the exact
cross-test-contamination reason retail_cheque_lifecycle_test.py's own
docstring gives for its identical split from retail_v32_cheque_migration_
test.py.

ENGINEERING.md's own rule applies throughout: a test that passes whether or
not the feature works is worse than none. Every guard below asserts a COUNT
or a stored value (never merely a status code) where the design calls for
it, and each docstring names the exact mutation that must turn it red.

Run (one file per process, AUDIT-010):
    pytest products/retail/tests/retail_quotation_test.py -v
"""
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import pytest

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_quotation_"))
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
TODAY = datetime.now().strftime('%Y-%m-%d')
YESTERDAY = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
TOMORROW = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')


# ── fixture bedrock (mirrors retail_cheque_lifecycle_test.py / retail_route_
#    capability_matrix_test.py's own `_make_user`) ───────────────────────────

def _make_user(role, *, company_id=None):
    email = f"quo-{role}-{uuid.uuid4().hex[:10]}@test.local"
    password = "QuotationLifecyclePW1"
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
    """A fresh, logged-in ADMIN client for its own newly-created company --
    one company per test, so every count/scoping assertion is against rows
    this test alone wrote. Admin holds every capability including
    CAP_DISCOUNT, so most tests use this fixture and switch to a dedicated
    manager/cashier pair only when the test is specifically ABOUT authority
    (tests 2, 12, 15, 20)."""
    c, _company_id, _user_id = _make_user('admin')
    return c


def _company_id_of(client):
    with client.session_transaction() as sess:
        return sess.get('company_id') or sess.get('mt_company_id')


def _create_product(client, sell_price=100.0, tax_rate=0.0, initial_stock=1000):
    r = client.post(f'{API}/products', json={
        'name': f'Quotation Test Product {uuid.uuid4().hex[:6]}',
        'sku': f'QTP-{uuid.uuid4().hex[:8]}',
        'cost_price': sell_price / 2, 'sell_price': sell_price, 'tax_rate': tax_rate,
        'initial_stock': initial_stock,
    })
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _set_sell_price(product_id, price):
    conn = get_retail_conn()
    conn.execute("UPDATE products SET sell_price=? WHERE id=?", (price, product_id))
    conn.commit()
    conn.close()


def _set_tax_rate(product_id, rate):
    conn = get_retail_conn()
    conn.execute("UPDATE products SET tax_rate=? WHERE id=?", (rate, product_id))
    conn.commit()
    conn.close()


def _create_customer(client, name=None):
    name = name or f'Quotation Customer {uuid.uuid4().hex[:6]}'
    r = client.post(f'{API}/customers', json={'name': name})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _create_quotation(client, items, **extra):
    body = dict({'items': items}, **extra)
    return client.post(f'{API}/quotations', json=body)


def _send(client, qid):
    return client.post(f'{API}/quotations/{qid}/send')


def _accept(client, qid):
    return client.post(f'{API}/quotations/{qid}/accept')


def _decline(client, qid):
    return client.post(f'{API}/quotations/{qid}/decline')


def _cancel(client, qid):
    return client.post(f'{API}/quotations/{qid}/cancel')


def _get_quotation(client, qid):
    return client.get(f'{API}/quotations/{qid}')


def _convert(client, qid, *, honour=None, override_expiry=None, idempotency_key=None):
    body = {'quotation_id': qid}
    if honour is not None:
        body['honour_quoted_prices'] = honour
    if override_expiry is not None:
        body['override_expiry'] = override_expiry
    if idempotency_key is not None:
        body['idempotency_key'] = idempotency_key
    return client.post(f'{API}/sales', json=body)


def _make_and_send_quotation(client, items, **extra):
    """CREATE then SEND in one call -- the shape most conversion tests need
    (`sent` is the minimum status a conversion accepts)."""
    r = _create_quotation(client, items, **extra)
    assert r.status_code == 201, r.get_json()
    qid = r.get_json()['data']['id']
    r2 = _send(client, qid)
    assert r2.status_code == 200, r2.get_json()
    return qid


def _sales_count(company_id):
    conn = get_retail_conn()
    n = conn.execute("SELECT COUNT(*) FROM sales WHERE company_id=?", (company_id,)).fetchone()[0]
    conn.close()
    return n


def _inventory_movements_count(company_id):
    conn = get_retail_conn()
    n = conn.execute("SELECT COUNT(*) FROM inventory_movements WHERE company_id=?", (company_id,)).fetchone()[0]
    conn.close()
    return n


def _quotation_row(qid):
    conn = get_retail_conn()
    row = conn.execute("SELECT * FROM sales_quotations WHERE id=?", (qid,)).fetchone()
    conn.close()
    return dict(row) if row else None


def _sale_item_row(sale_id):
    conn = get_retail_conn()
    row = conn.execute("SELECT * FROM sale_items WHERE sale_id=?", (sale_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def _sale_row(sale_id):
    conn = get_retail_conn()
    row = conn.execute("SELECT * FROM sales WHERE id=?", (sale_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def _set_valid_until(qid, value):
    conn = get_retail_conn()
    conn.execute("UPDATE sales_quotations SET valid_until=? WHERE id=?", (value, qid))
    conn.commit()
    conn.close()


# ── 1. Expired quote refuses conversion, and refuses it CLEANLY ─────────────

def test_expired_quote_refuses_conversion(client):
    """MUTATION A: change the gate from `valid_until < today` to `<=` -- the
    expires-TODAY boundary flips (a quote expiring today, still legally
    valid for the rest of the day, would be wrongly refused). Proven
    DIRECTLY below by converting a `valid_until == TODAY` quote and
    requiring 200 -- MUTATION A turns that half red on its own; it would
    NOT be caught by the YESTERDAY case alone, which is why both live in
    this one test rather than splitting the boundary out where it could be
    quietly dropped.
    MUTATION B: `if False:` in place of the expiry check -> the row-count
    assertions below go red while a status-code-only test would still pass.
    """
    pid = _create_product(client, sell_price=50.0)
    cid = _company_id_of(client)

    # The boundary half: expires TODAY is still valid for the rest of today.
    qid_today = _make_and_send_quotation(client, [{'product_id': pid, 'quantity': 1}])
    _set_valid_until(qid_today, TODAY)
    r_today = _convert(client, qid_today)
    assert r_today.status_code == 200, r_today.get_json()

    # The refusal half: yesterday is unambiguously expired.
    qid = _make_and_send_quotation(client, [{'product_id': pid, 'quantity': 1}])
    _set_valid_until(qid, YESTERDAY)

    sales_before = _sales_count(cid)
    movements_before = _inventory_movements_count(cid)

    r = _convert(client, qid)
    assert r.status_code == 409, r.get_json()
    assert r.get_json().get('code') == 'QUOTATION_EXPIRED', r.get_json()
    # A status code alone cannot distinguish "the expiry gate refused" from
    # "something else refused" -- these two counts are what actually prove
    # the SALE never happened.
    assert _sales_count(cid) == sales_before
    assert _inventory_movements_count(cid) == movements_before


# ── 2. Overriding expiry requires CAP_DISCOUNT -- BOTH directions ──────────

def test_expiry_override_requires_cap_discount():
    """MUTATION A: hardcode the CAP_DISCOUNT check to `False` -> the MANAGER
    case (the allow-half) goes red. This is the mutation that passes every
    deny-test while silently destroying the shop's ability to honour its
    own quote.
    MUTATION B: hardcode the check to `True` -> the cashier (deny-half) case
    goes red.
    """
    # create_product needs CAP_STOCK_ADJUST, which neither cashier nor
    # manager-issuing-a-quote is being tested for here -- an admin in the
    # SAME company creates the product; each role's OWN client then owns
    # the quotation lifecycle (create/send/convert) that this test is
    # actually about.
    cashier_client, cashier_cid, _ = _make_user('cashier')
    admin_for_cashier, _, _ = _make_user('admin', company_id=cashier_cid)
    manager_client, manager_cid, _ = _make_user('manager')
    admin_for_manager, _, _ = _make_user('admin', company_id=manager_cid)

    cases = (
        (cashier_client, admin_for_cashier, cashier_cid, False),
        (manager_client, admin_for_manager, manager_cid, True),
    )
    for role_client, admin_client, cid, should_succeed in cases:
        pid = _create_product(admin_client, sell_price=40.0)
        qid = _make_and_send_quotation(role_client, [{'product_id': pid, 'quantity': 1}])
        _set_valid_until(qid, YESTERDAY)

        sales_before = _sales_count(cid)
        r = _convert(role_client, qid, override_expiry=True)
        if should_succeed:
            assert r.status_code == 200, r.get_json()
            assert _sales_count(cid) == sales_before + 1
        else:
            assert r.status_code == 403, r.get_json()
            assert _sales_count(cid) == sales_before


# ── 3. override_expiry on a LIVE quote is not refused (N4) ─────────────────

def test_override_expiry_on_a_live_quote_is_not_refused():
    """MUTATION: change the gate from `is_expired and override_expiry` to
    `override_expiry` alone -> a cashier flipping this flag on an ordinary,
    non-expired conversion would be wrongly 403'd -> RED.
    """
    cashier_client, cashier_cid, _ = _make_user('cashier')
    admin_client, _, _ = _make_user('admin', company_id=cashier_cid)
    pid = _create_product(admin_client, sell_price=30.0)
    qid = _make_and_send_quotation(cashier_client, [{'product_id': pid, 'quantity': 1}])
    # valid_until defaults to NULL (no expiry) -- never expired.

    sales_before = _sales_count(cashier_cid)
    r = _convert(cashier_client, qid, override_expiry=True)
    assert r.status_code == 200, r.get_json()
    assert _sales_count(cashier_cid) == sales_before + 1


# ── 4/5/6. PRICING: whole winning side, never compounded ───────────────────

def test_honour_never_charges_above_todays_price(client):
    """Quote 12.00@0%, live drops to 9.00@0%, honour -> unit_price==9.00,
    discount_pct==0, and the line named in repriced_lines with winner:'live'.
    MUTATION: take the quoted side unconditionally -> RED (unit_price would
    read 12.00)."""
    pid = _create_product(client, sell_price=12.0)
    qid = _make_and_send_quotation(client, [{'product_id': pid, 'quantity': 1}])
    _set_sell_price(pid, 9.0)

    r = _convert(client, qid, honour=True)
    assert r.status_code == 200, r.get_json()
    sale_id = r.get_json()['data']['id']
    item = _sale_item_row(sale_id)
    assert item['unit_price'] == 9.0
    assert item['discount_pct'] == 0
    repriced = r.get_json()['data'].get('repriced_lines') or []
    assert len(repriced) == 1, repriced
    assert repriced[0]['winner'] == 'live'


def test_honour_holds_the_promise_below_todays_price(client):
    """Quote 12.00@0%, live rises to 15.00, honour -> unit_price==12.00.
    MUTATION: take the live side unconditionally -> RED (unit_price would
    read 15.00). Together with the test above, this is the both-directions
    proof of the ONE expression the feature turns on."""
    pid = _create_product(client, sell_price=12.0)
    qid = _make_and_send_quotation(client, [{'product_id': pid, 'quantity': 1}])
    _set_sell_price(pid, 15.0)

    r = _convert(client, qid, honour=True)
    assert r.status_code == 200, r.get_json()
    sale_id = r.get_json()['data']['id']
    item = _sale_item_row(sale_id)
    assert item['unit_price'] == 12.0


def test_honour_takes_the_whole_winning_side_and_never_compounds(client):
    """Quote 12.00 @ 10% (effective 10.80), live 9.00 @ 0% (effective 9.00),
    honour -> unit_price==9.00 AND discount_pct==0, so line_total==9.00*qty.
    Assert it is NOT 8.10.
    MUTATION: min() on raw unit_price with the quoted discount_pct carried
    over -> RED at 8.10. This is the defect the ORIGINAL submitted design
    shipped; without this test the feature undercharges every discounted
    quote whose shelf price fell.
    """
    pid = _create_product(client, sell_price=12.0)
    qid = _make_and_send_quotation(client, [{'product_id': pid, 'quantity': 1, 'discount_pct': 10}])
    _set_sell_price(pid, 9.0)

    r = _convert(client, qid, honour=True)
    assert r.status_code == 200, r.get_json()
    sale_id = r.get_json()['data']['id']
    item = _sale_item_row(sale_id)
    assert item['unit_price'] == 9.0
    assert item['discount_pct'] == 0
    assert item['line_total'] == 9.0, f"expected 9.00, the compounding bug would give 8.10; got {item['line_total']}"
    assert item['line_total'] != 8.10


def test_tax_rate_is_always_live_never_the_snapshot(client):
    """Quote at 16% tax, shop tax changed to 10%, honour -> sale_items.
    tax_rate == 0.10 (stored as a percentage figure, matching every other
    tax_rate column in this product -- see products.tax_rate itself).
    MUTATION: carry tax_rate from the quoted line instead of re-reading the
    live product row -> RED. Tax is law, not a promise."""
    pid = _create_product(client, sell_price=10.0, tax_rate=16)
    qid = _make_and_send_quotation(client, [{'product_id': pid, 'quantity': 1}])
    _set_tax_rate(pid, 10)

    r = _convert(client, qid, honour=True)
    assert r.status_code == 200, r.get_json()
    sale_id = r.get_json()['data']['id']
    item = _sale_item_row(sale_id)
    assert item['tax_rate'] == 10, item


def test_live_mode_uses_live_price_and_records_variance(client):
    """Same quote (12.00@0%), live rises to 15.00, honour_quoted_prices:
    false -> unit_price == 15.00 AND sales_quotations.conversion_variance_
    json names the delta with mode:'live'.
    MUTATION: drop the variance write -> RED. This is what stops the
    reprice being silent."""
    pid = _create_product(client, sell_price=12.0)
    qid = _make_and_send_quotation(client, [{'product_id': pid, 'quantity': 1}])
    _set_sell_price(pid, 15.0)

    r = _convert(client, qid, honour=False)
    assert r.status_code == 200, r.get_json()
    sale_id = r.get_json()['data']['id']
    item = _sale_item_row(sale_id)
    assert item['unit_price'] == 15.0

    q = _quotation_row(qid)
    assert q['conversion_variance_json'], 'conversion_variance_json was never written'
    variance = json.loads(q['conversion_variance_json'])
    assert variance['mode'] == 'live', variance


# ── 9. Stock check is NOT special-cased for quotes ──────────────────────────

def test_stock_check_is_not_special_cased_for_quotes(client):
    """Quote for 10, on hand 3 (no sync configured, so no relaxation
    applies) -> the SAME 'Insufficient stock' 400 an ordinary cart gets.
    MUTATION: add any quote-specific bypass -> RED. A guard against the
    future 'helpful' special case, not against today's code."""
    cid = _company_id_of(client)
    pid = _create_product(client, sell_price=20.0, initial_stock=3)
    qid = _make_and_send_quotation(client, [{'product_id': pid, 'quantity': 10}])

    sales_before = _sales_count(cid)
    r = _convert(client, qid, honour=False)
    assert r.status_code == 400, r.get_json()
    assert 'Insufficient stock' in r.get_json().get('message', ''), r.get_json()
    assert _sales_count(cid) == sales_before


# ── 10/11. Second conversion is refused; the flip is guarded structurally ──

def test_second_conversion_is_refused_by_the_status_check(client):
    """Two SEQUENTIAL conversion requests: exactly ONE sales row, one
    matching set of inventory_movements, status=='converted' once, second
    request 409.

    STATES WHAT IT PROVES, HONESTLY: that at most one sale EVER results,
    ordinarily -- not, on its own, WHICH of the two guards did it. MEASURED
    directly: removing the `_resolve_quotation_for_conversion` pre-check
    alone does NOT turn this test red, because the flip UPDATE's own `AND
    status IN ('sent','accepted')` + rowcount check (create_sale) is
    defence-in-depth that independently produces the identical observable
    409/one-sale outcome in this sequential scenario -- the two guards are
    genuinely redundant here, which is a property of this implementation,
    not a gap in it. Test 11 is what isolates EACH guard: it bypasses the
    pre-check specifically (not just removes a line -- see its own
    docstring for why a naive removal cannot be exercised in isolation
    either) and proves the flip UPDATE alone still refuses the second
    request.
    MUTATION: remove BOTH the pre-check AND the flip UPDATE's status clause
    -> RED with two sales rows. Removing only one leaves the other standing
    and this test stays green, which is exactly why it does not claim to
    isolate either alone.
    """
    cid = _company_id_of(client)
    pid = _create_product(client, sell_price=25.0)
    qid = _make_and_send_quotation(client, [{'product_id': pid, 'quantity': 1}])

    r1 = _convert(client, qid, honour=False)
    assert r1.status_code == 200, r1.get_json()
    sale_id = r1.get_json()['data']['id']

    movements_after_first = _inventory_movements_count(cid)

    r2 = _convert(client, qid, honour=False)
    assert r2.status_code == 409, r2.get_json()

    assert _sales_count(cid) == 1
    assert _inventory_movements_count(cid) == movements_after_first
    q = _quotation_row(qid)
    assert q['status'] == 'converted'
    assert q['converted_sale_uid'] == _sale_row(sale_id)['uid']


def test_the_conversion_flip_is_guarded_by_its_own_rowcount():
    """Structural + behavioural: the flip UPDATE carries
    `AND status IN ('sent','accepted')` and its `cur.rowcount` result
    reaches a branch that rolls back the WHOLE sale.

    The behavioural half BYPASSES THE PRE-CHECK SPECIFICALLY, by replacing
    `_resolve_quotation_for_conversion` with a stand-in that fetches the
    same row and lines but never refuses on status -- exactly what a future
    edit that weakens or removes the pre-check would look like from create_
    sale's point of view. A hand-flipped 'converted' status (the first
    version of this test) does NOT achieve this: the pre-check reads that
    same status and refuses on its own, so the rowcount guard is never
    actually exercised -- MEASURED directly, that version stays green even
    when the flip UPDATE's own status clause is deleted.
    MUTATION: drop the status clause from the flip UPDATE, or stop checking
    its rowcount -> RED, because with the pre-check bypassed the second
    conversion would then succeed outright (two sales) instead of 409ing
    with the second sale rolled back.
    """
    import inspect
    import api.retail_api as retail_api_module

    src = inspect.getsource(retail_api_module.create_sale)
    assert "status IN ('sent','accepted')" in src, (
        "create_sale's conversion UPDATE no longer carries the status guard clause"
    )
    assert "cur.rowcount != 1" in src, (
        "create_sale's conversion UPDATE result is not checked for rowcount"
    )

    c, cid, _ = _make_user('admin')
    pid = _create_product(c, sell_price=60.0)
    qid = _make_and_send_quotation(c, [{'product_id': pid, 'quantity': 1}])

    def _resolve_without_status_check(conn, cid_, qid_):
        """The pre-check, MINUS the status refusal -- everything else
        (existence, lines, expiry) unchanged. This is what "the pre-check
        was weakened" looks like to create_sale's own caller."""
        row = conn.execute(
            "SELECT * FROM sales_quotations WHERE id=? AND company_id=?", (qid_, cid_)
        ).fetchone()
        if not row:
            return None, None, False, (
                retail_api_module.jsonify({'status': 'error', 'message': 'Quotation not found'}), 404)
        lines = conn.execute(
            "SELECT * FROM sales_quotation_lines WHERE quotation_id=? ORDER BY line_no", (qid_,)
        ).fetchall()
        return row, lines, False, None

    import unittest.mock as mock
    with mock.patch.object(retail_api_module, '_resolve_quotation_for_conversion',
                            side_effect=_resolve_without_status_check):
        r1 = _convert(c, qid, honour=False)
        assert r1.status_code == 200, r1.get_json()
        r2 = _convert(c, qid, honour=False)
        assert r2.status_code == 409, r2.get_json()

    assert _sales_count(cid) == 1, (
        'with the pre-check bypassed, the flip UPDATE\'s own status clause + rowcount check '
        'must still be the thing that refuses the second conversion and rolls back its sale '
        'entirely -- a count of 2 here means the rowcount guard is not actually load-bearing')


# ── 12. Discount authority is checked at issuance, not at honouring ────────

def test_a_cashier_may_convert_a_manager_issued_discounted_quote():
    """Manager creates a quote carrying 20% off; a cashier (no CAP_DISCOUNT)
    converts it -> 200, one sale, sale_items.discount_pct == 20.
    MUTATION: extend `wants_discount` to also cover quotation lines being
    converted -> RED. Discount authority is checked when the promise is
    MADE, not when it is HONOURED; a deny-only proof here would silently
    break the shop's own paperwork.
    """
    manager_client, cid, _ = _make_user('manager')
    cashier_client, _, _ = _make_user('cashier', company_id=cid)
    pid = _create_product(manager_client, sell_price=50.0)

    qid = _make_and_send_quotation(manager_client, [{'product_id': pid, 'quantity': 1, 'discount_pct': 20}])

    sales_before = _sales_count(cid)
    r = _convert(cashier_client, qid, honour=True)
    assert r.status_code == 200, r.get_json()
    assert _sales_count(cid) == sales_before + 1
    sale_id = r.get_json()['data']['id']
    item = _sale_item_row(sale_id)
    assert item['discount_pct'] == 20


# ── 13. Expiry is derived, not stored ───────────────────────────────────────

def test_expired_is_derived_not_stored(client):
    """Freeze past valid_until, GET -> is_expired: true while status still
    reads 'sent'; move valid_until forward again, GET again -> is_expired:
    false and status still 'sent'.
    MUTATION: persist status='expired' on read -> the "move forward" half
    goes red while a one-direction test would pass."""
    pid = _create_product(client, sell_price=10.0)
    qid = _make_and_send_quotation(client, [{'product_id': pid, 'quantity': 1}])

    _set_valid_until(qid, YESTERDAY)
    r1 = _get_quotation(client, qid)
    assert r1.status_code == 200
    assert r1.get_json()['data']['quotation']['is_expired'] is True
    assert r1.get_json()['data']['quotation']['status'] == 'sent'

    _set_valid_until(qid, TOMORROW)
    r2 = _get_quotation(client, qid)
    assert r2.status_code == 200
    assert r2.get_json()['data']['quotation']['is_expired'] is False
    assert r2.get_json()['data']['quotation']['status'] == 'sent'


# ── 14. A sent quotation is immutable ───────────────────────────────────────

def test_sent_quotation_is_immutable(client):
    """PUT a sent quotation -> 409, and re-read the lines to prove none
    changed.
    MUTATION: allow the edit while status=='sent' -> RED."""
    pid = _create_product(client, sell_price=10.0)
    qid = _make_and_send_quotation(client, [{'product_id': pid, 'quantity': 2}])
    before = _get_quotation(client, qid).get_json()['data']['lines']

    r = client.put(f'{API}/quotations/{qid}', json={'items': [{'product_id': pid, 'quantity': 99}]})
    assert r.status_code == 409, r.get_json()

    after = _get_quotation(client, qid).get_json()['data']['lines']
    assert after == before


# ── 15. Discount on a NEW quote requires CAP_DISCOUNT ──────────────────────

def test_discount_on_quote_requires_cap_discount():
    """Cashier POSTs a quote carrying discount_pct: 20 -> 403 and ZERO
    sales_quotations rows; manager -> 201. Also assert a cashier's
    discount_pct: -5 SUCCEEDS (the clamp floors it at 0).
    MUTATION A: remove the inline check -> cashier case goes red.
    MUTATION B: judge the raw field instead of the clamped one -> the -5
    case goes red.
    """
    cashier_client, cashier_cid, _ = _make_user('cashier')
    admin_client, _, _ = _make_user('admin', company_id=cashier_cid)
    manager_client, manager_cid, _ = _make_user('manager')

    pid_c = _create_product(admin_client, sell_price=10.0)
    before = _q_count(cashier_cid)
    r = _create_quotation(cashier_client, [{'product_id': pid_c, 'quantity': 1, 'discount_pct': 20}])
    assert r.status_code == 403, r.get_json()
    assert _q_count(cashier_cid) == before

    pid_m = _create_product(manager_client, sell_price=10.0)
    r2 = _create_quotation(manager_client, [{'product_id': pid_m, 'quantity': 1, 'discount_pct': 20}])
    assert r2.status_code == 201, r2.get_json()

    # Clamp floors a negative at 0 -- asks for NO discount at all, so a
    # cashier issuing one must succeed.
    r3 = _create_quotation(cashier_client, [{'product_id': pid_c, 'quantity': 1, 'discount_pct': -5}])
    assert r3.status_code == 201, r3.get_json()


def _q_count(company_id):
    conn = get_retail_conn()
    n = conn.execute("SELECT COUNT(*) FROM sales_quotations WHERE company_id=?", (company_id,)).fetchone()[0]
    conn.close()
    return n


# ── 16. The inline capability verdict is CONSUMED, not merely computed (C3) ─

def test_the_inline_capability_verdict_is_consumed_not_merely_computed():
    """Runs retail_capability_ratchet_ast.py's own analysis over
    create_quotation, update_quotation and create_sale's inline
    CAP_DISCOUNT checks, and asserts each is recognised as a real gate.
    MUTATION: rewrite any of the three as `_unused = session_has_capability
    (CAP_DISCOUNT)` -> RED. This is the exact dead-line spelling that
    defeated all nineteen money-disclosure tests once (see that ratchet
    module's own docstring)."""
    import ast
    sys.path.insert(0, str(PRODUCT_DIR / 'tests'))
    import retail_capability_ratchet_ast as ratchet

    src = (BACKEND_DIR / 'api' / 'retail_api.py').read_text(encoding='utf-8')
    tree = ast.parse(src)
    handlers = {fn.name: fn for fn in ast.walk(tree) if isinstance(fn, ast.FunctionDef)}

    for name in ('create_quotation', 'update_quotation', 'create_sale'):
        assert name in handlers, f'{name} not found in retail_api.py'
        detail = ratchet.capability_check_detail(handlers[name])
        kind, description = detail
        assert kind in ('decorator', 'in-handler'), (
            f'{name}: no capability gate recognised as consumed (got kind={kind!r}, {description!r})')


# ── 17. Committed demand counts only OPEN, ACCEPTED quotations ─────────────

def test_committed_demand_counts_only_open_accepted_quotations(client):
    """Fixture of five: draft, sent, accepted-open, accepted-and-converted,
    accepted-but-expired -- only the accepted-open quantity comes back.
    MUTATION A: drop `converted_sale_uid IS NULL` -> the converted one
    double-counts, the number that makes a shop over-order.
    MUTATION B: drop `status='accepted'` -> draft and sent leak in.
    MUTATION C: drop the valid_until clause -> the expired one leaks in.
    """
    pid = _create_product(client, sell_price=10.0, initial_stock=10000)
    branches = client.get(f'{API}/branches').get_json()['data']
    bid = branches[0]['id']

    # draft
    _create_quotation(client, [{'product_id': pid, 'quantity': 1}])
    # sent (not accepted)
    _make_and_send_quotation(client, [{'product_id': pid, 'quantity': 2}])
    # accepted, open
    qid_open = _make_and_send_quotation(client, [{'product_id': pid, 'quantity': 4}])
    _accept(client, qid_open)
    # accepted, then converted
    qid_conv = _make_and_send_quotation(client, [{'product_id': pid, 'quantity': 8}])
    _accept(client, qid_conv)
    r = _convert(client, qid_conv, honour=False)
    assert r.status_code == 200, r.get_json()
    # accepted, but expired
    qid_expired = _make_and_send_quotation(client, [{'product_id': pid, 'quantity': 16}])
    _accept(client, qid_expired)
    _set_valid_until(qid_expired, YESTERDAY)

    r = client.get(f'{API}/quotations/committed-demand?branch_id={bid}')
    assert r.status_code == 200, r.get_json()
    rows = {row['product_id']: row['committed'] for row in r.get_json()['data']}
    assert rows.get(pid) == 4, rows


# ── 18. Every quotation route is company-scoped ─────────────────────────────

def test_quotation_rows_are_company_scoped():
    """Company A cannot GET, PUT, send, accept, decline, cancel,
    prepare-convert or convert company B's quotation by id.
    MUTATION: remove `AND company_id=?` from any one handler -> RED."""
    client_a, _cid_a, _ = _make_user('admin')
    client_b, _cid_b, _ = _make_user('admin')

    pid = _create_product(client_a, sell_price=10.0)
    r = _create_quotation(client_a, [{'product_id': pid, 'quantity': 1}])
    qid = r.get_json()['data']['id']

    assert _get_quotation(client_b, qid).status_code == 404
    assert client_b.put(f'{API}/quotations/{qid}', json={'items': [{'product_id': pid, 'quantity': 1}]}).status_code == 404
    assert _send(client_b, qid).status_code == 404
    # Must be sent for accept/decline/cancel/convert to have any chance of
    # succeeding at all on the OWNING company -- sent here on company A so a
    # 404 from company B is unambiguously the scoping guard, not the status
    # guard.
    _send(client_a, qid)
    assert _accept(client_b, qid).status_code == 404
    assert _decline(client_b, qid).status_code == 404
    assert _cancel(client_b, qid).status_code == 404
    assert client_b.post(f'{API}/quotations/{qid}/prepare-conversion').status_code == 404
    assert _convert(client_b, qid, honour=False).status_code == 404


# ── 19. A walk-in quotation survives the customer JOIN ─────────────────────

def test_walk_in_quotations_survive_the_customer_join(client):
    """A quotation with customer_id IS NULL appears in GET /quotations.
    MUTATION: move the company condition from the JOIN to the WHERE -> RED,
    the row vanishes. The exact trap documented on list_purchase_orders."""
    pid = _create_product(client, sell_price=10.0)
    r = _create_quotation(client, [{'product_id': pid, 'quantity': 1}])
    assert r.status_code == 201, r.get_json()
    qid = r.get_json()['data']['id']

    rows = client.get(f'{API}/quotations').get_json()['data']
    ids = [row['id'] for row in rows]
    assert qid in ids, ids
    row = next(row for row in rows if row['id'] == qid)
    assert row['customer_id'] is None
    assert row.get('customer_name') is None


# ── 20. A pinned till files its quotation under its own branch (B4) ───────

def test_a_pinned_till_files_its_quotation_under_its_own_branch():
    """Pin the device to branch 2, POST a quotation with NO branch_id -> the
    row lands on branch 2, not the company's first branch. Then, as a
    branch-scoped user, POST with an explicit foreign branch_id -> 403 'You
    may only write to your own branch', zero rows.
    MUTATION: replace _resolve_working_branch(...) with
    data.get('branch_id') or _default_branch(...) -> BOTH halves go red.
    """
    client_a, cid, _ = _make_user('admin')
    # Force the company's default branch to self-heal BEFORE branch 2 is
    # created -- _default_branch()/POST /products only invents "Main
    # Branch" the first time something actually needs a branch, so a company
    # that has never sold or stocked anything has ZERO branches, not one.
    # Without this, POST /branches's own new row would be the ONLY branch
    # in the company and there would be no genuine "first branch" left to
    # name branch1_id.
    _create_product(client_a, sell_price=10.0)
    r = client_a.post(f'{API}/branches', json={'name': 'Second Branch', 'address': '', 'phone': ''})
    assert r.status_code == 200, r.get_json()
    branch2_id = r.get_json()['data']['id']
    branches = client_a.get(f'{API}/branches').get_json()['data']
    branch1_id = next(b['id'] for b in branches if b['id'] != branch2_id)
    branch2_uid = next(b['uid'] for b in branches if b['id'] == branch2_id)

    import unittest.mock as mock
    with mock.patch('api.retail_api._onboarding_get_device_branch_uid', return_value=branch2_uid):
        pid = _create_product(client_a, sell_price=10.0)
        r = _create_quotation(client_a, [{'product_id': pid, 'quantity': 1}])
        assert r.status_code == 201, r.get_json()
        qid = r.get_json()['data']['id']
        q = _quotation_row(qid)
        assert q['branch_id'] == branch2_id, q

    # Branch-scoped user attempting to write an explicit, FOREIGN branch_id.
    with mock.patch('api.retail_api.session_branch_scope', return_value=branch2_uid):
        before = _q_count(cid)
        pid2 = _create_product(client_a, sell_price=10.0)
        r2 = _create_quotation(client_a, [{'product_id': pid2, 'quantity': 1}], branch_id=branch1_id)
        assert r2.status_code == 403, r2.get_json()
        assert 'own branch' in r2.get_json().get('message', ''), r2.get_json()
        assert _q_count(cid) == before


# ── 21. doc_number collision is contained, not fatal ───────────────────────

def test_doc_number_collision_is_contained_not_fatal(client):
    """Force a doc_number collision; assert 409 with the connection
    released, NOT a 500 with a leaked WAL write lock -- then assert a
    SUBSEQUENT unrelated write to the same database still succeeds.
    MUTATION: remove the `except sqlite3.IntegrityError` containment ->
    RED. Reproduces in miniature the outage where one colliding PO took the
    whole install down (retail_api.py's own create_purchase_order comment).
    """
    pid = _create_product(client, sell_price=10.0)
    r = _create_quotation(client, [{'product_id': pid, 'quantity': 1}])
    assert r.status_code == 201, r.get_json()
    existing_doc_number = r.get_json()['data']['doc_number']

    import unittest.mock as mock
    with mock.patch('api.retail_api._next_ref', return_value=existing_doc_number.rsplit('-', 2)[0]):
        # Same _next_ref output + same company/device fragments -> the exact
        # same doc_number as the row already committed above.
        r2 = _create_quotation(client, [{'product_id': pid, 'quantity': 1}])
        assert r2.status_code == 409, r2.get_json()

    # The connection from the failed attempt must have been released -- a
    # subsequent, unrelated write on the SAME database must still succeed.
    r3 = _create_quotation(client, [{'product_id': pid, 'quantity': 1}])
    assert r3.status_code == 201, r3.get_json()


# ── 22. converted_sale_uid is the WIRE identity ─────────────────────────────

def test_converted_sale_uid_is_the_wire_identity(client):
    """Assert sales_quotations.converted_sale_uid == sales.uid, and
    explicitly assert it does NOT equal str(sales.id).
    MUTATION: store sale_id (the local autoincrement) instead -> RED. Cheap,
    and it catches the one bug invisible on a single-device install and
    wrong on every second device."""
    pid = _create_product(client, sell_price=10.0)
    qid = _make_and_send_quotation(client, [{'product_id': pid, 'quantity': 1}])
    r = _convert(client, qid, honour=False)
    assert r.status_code == 200, r.get_json()
    sale_id = r.get_json()['data']['id']
    sale = _sale_row(sale_id)

    q = _quotation_row(qid)
    assert q['converted_sale_uid'] == sale['uid']
    assert q['converted_sale_uid'] != str(sale_id)


# ── 23. The conversion variance survives an idempotent retry (C8) ─────────

def test_the_conversion_variance_survives_an_idempotent_retry(client):
    """Convert, then re-POST with the same idempotency_key; assert the
    second response carries only {id, sale_number} (unchanged behaviour)
    AND that sales_quotations.conversion_variance_json still reads back
    complete.
    MUTATION: make the conversion sheet read repriced_lines off the sale
    response -- proven wrong here because the retry path carries none."""
    pid = _create_product(client, sell_price=12.0)
    qid = _make_and_send_quotation(client, [{'product_id': pid, 'quantity': 1}])
    _set_sell_price(pid, 15.0)
    idem = str(uuid.uuid4())

    r1 = _convert(client, qid, honour=True, idempotency_key=idem)
    assert r1.status_code == 200, r1.get_json()
    assert r1.get_json()['data'].get('repriced_lines'), 'first response should carry repriced_lines'

    r2 = _convert(client, qid, honour=True, idempotency_key=idem)
    assert r2.status_code == 200, r2.get_json()
    assert set(r2.get_json()['data'].keys()) == {'id', 'sale_number'}, (
        f"idempotent replay must return only {{id, sale_number}}; got {r2.get_json()['data'].keys()}")

    q = _quotation_row(qid)
    assert q['conversion_variance_json']
    variance = json.loads(q['conversion_variance_json'])
    assert variance['lines'], 'conversion_variance_json must still carry the per-line variance on retry'
