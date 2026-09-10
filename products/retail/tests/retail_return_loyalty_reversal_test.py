"""
Aura Retail -- loyalty reversal on returns (schema v27, ROADMAP.md's
2026-08-31 "RETURNS AGAINST A SALE THAT USED POINTS -- decided, not yet
built" entry, now built inside `create_return`).

A returned sale owes TWO loyalty consequences, and both are owed:
  1. Points REDEEMED on that sale come back to the customer -- they paid
     with them, so refunding the cash and keeping the points would be
     taking payment twice.
  2. Points EARNED on that sale are clawed back -- otherwise
     buy/earn/return/keep is a free points generator that costs the shop
     real money at the next redemption.

Both land as NEW, immutable `loyalty_ledger` rows (`entry_type='adjust'`),
never as an edit to the original 'earn'/'redeem' rows.

WHAT THIS FILE PROVES, in order (numbered to match this feature's own
report):

  1. A full return of a sale that redeemed points gives the points back
     EXACTLY.
  2. A full return of a sale that earned points claws them back EXACTLY.
  3. A partial return settles BOTH reversals proportionally to the
     RETURNED VALUE, not the line count -- returning the cheap half of a
     two-line basket (25% of the value, 50% of the lines) claws back/gives
     back 25%, not 50%. This is also the file's differential proof against
     a line-count-based regression: the two lines are priced so a
     value-based and a count-based split disagree, so a regression to
     counting lines instead of value flips this test red on its own,
     without needing a source-level mutation.
  4. A claw-back is allowed to drive the ledger (and the
     `customers.loyalty_points` cache) NEGATIVE -- it is never clamped at
     zero.
  5. Returning a walk-in sale (no customer_id at all) leaves
     `loyalty_ledger` completely untouched.
  6. Returning a sale that wrote no ledger rows at all (the pre-v27 shape:
     a customer_id is present, but zero 'earn'/'redeem' rows exist for
     that sale_id) does not crash and invents no reversal.
  7. The ledger balance after each return equals what you would compute by
     hand -- asserted explicitly alongside cases 1-4 above rather than as a
     separate case, since every one of them already carries the by-hand
     arithmetic in its own assertions/comments.

Mutation-prove (see this session's own report for the two runs, not
encoded here as source-mutating test code -- there is no clean seam to
monkeypatch inline arithmetic the way
`retail_loyalty_redemption_test.py`'s capability tests monkeypatch a whole
function): the proportional split (case 3) and the no-clamp rule (case 4)
were each confirmed to go RED under a deliberate regression
(line-count-based split; `max(0, ...)` clamp) and back to GREEN on revert.

Self-contained bootstrap, matching retail_loyalty_redemption_test.py and
the rest of this suite (no shared conftest.py exists here). CRITICAL:
exactly ONE pytest process per file -- AURA_APP_DATA resolves at import
time, so two test files sharing one pytest invocation corrupt each other.

Run:
    py -3.14 -m pytest products/retail/tests/retail_return_loyalty_reversal_test.py -v
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_return_loyalty_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402

seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")
os.environ.pop("AURA_RETAIL_DEMO_MODE", None)

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

import database.schema as sch  # noqa: E402
from commercial_runtime.identity import user_accounts  # noqa: E402
from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402

API = '/api/sub/retail'


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


# ── Fixtures / helpers (deliberately duplicated per-file convention -- see
#    retail_product_lookup_nocase_test.py's own module docstring for why
#    nothing here is shared via a conftest.py) ──────────────────────────────

def _make_user(role, company_id, capabilities=None):
    """A real account with real capability rows -- matches
    retail_loyalty_redemption_test.py's own `_make_user`."""
    email = f"retloy-{role}-{uuid.uuid4().hex[:10]}@test.local"
    password = "RetLoyaltyPW1"
    user_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, "
        "require_password_change) VALUES (?,?,?,?,?,?,?,0)",
        (user_id, company_id, f"EMP-{uuid.uuid4().hex[:6]}", email, hash_password(password), role, "active"),
    )
    conn.execute(
        "INSERT INTO user_permissions (id, user_id, subsystem, access_level) VALUES (?,?,?,?)",
        (str(uuid.uuid4()), user_id, "retail", "full"),
    )
    user_accounts.seed_capabilities_for_user(conn, user_id, role)
    if capabilities:
        for code, level in capabilities.items():
            conn.execute(
                "UPDATE user_permissions SET access_level=? WHERE user_id=? AND subsystem=?",
                (level, user_id, code),
            )
    conn.commit()
    conn.close()

    client = app.test_client()
    r = client.post('/api/auth/login', json={'email': email, 'password': password})
    assert r.status_code == 200, r.get_json()
    return client


def _new_shop():
    """A fresh company with one logged-in ADMIN user (bypasses every
    capability gate) -- each test gets its OWN company so ledger balances
    and settings from one test never leak into another's assertions."""
    company_id = str(uuid.uuid4())
    admin = _make_user('admin', company_id)
    admin.get(f'{API}/settings/tax')  # forces _ensure_credit_schema/doc_sequences, matching sibling files
    return company_id, admin


@pytest.fixture
def shop():
    return _new_shop()


def _create_product(client, sell_price=100.0, tax_rate=0):
    tag = uuid.uuid4().hex[:8]
    payload = {
        'name': f'Return-loyalty item {tag}', 'sku': f'RETLOY-{tag}',
        'sell_price': sell_price, 'cost_price': 1.0, 'tax_rate': tax_rate,
        'initial_stock': 1000,
    }
    r = client.post(f'{API}/products', json=payload)
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _create_customer(client):
    payload = {'name': f'Return Loyalty Customer {uuid.uuid4().hex[:6]}'}
    r = client.post(f'{API}/customers', json=payload)
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _sale(client, items, **extra):
    payload = dict({'items': items, 'payment_method': 'cash',
                     'idempotency_key': str(uuid.uuid4())}, **extra)
    return client.post(f'{API}/sales', json=payload)


def _return(client, sale_id, items, reason='test return'):
    payload = {'sale_id': sale_id, 'items': items, 'reason': reason,
               'idempotency_key': str(uuid.uuid4())}
    return client.post(f'{API}/returns', json=payload)


def _set_loyalty_point_value(client, value):
    r = client.post(f'{API}/settings/credit', json={'loyalty_point_value': value})
    assert r.status_code == 200, r.get_json()


def _seed_loyalty_balance(company_id, customer_id, points):
    """Seeds a STARTING balance the way the real `_migrate_add_loyalty_ledger`
    backfill does: an 'opening' ledger row AND the matching
    `customers.loyalty_points` cache value, kept IN SYNC from the start --
    exactly like a genuine pre-existing balance would be (the backfill reads
    the cache and copies it into the ledger; it never leaves the two
    disagreeing). This file asserts on both the ledger sum AND the cache
    column, so both must start in sync for the cache assertions to mean
    anything -- a ledger-only write (retail_loyalty_redemption_test.py's own
    `_grant_ledger_points`, fine there because that file never asserts on
    the cache column) would leave the cache stuck at 0 while the ledger
    carried the seeded balance."""
    conn = sch.get_retail_conn()
    conn.execute(
        "INSERT INTO loyalty_ledger (uid,company_id,customer_id,points_delta,entry_type,created_by) "
        "VALUES (?,?,?,?,'opening',?)",
        (str(uuid.uuid4()), company_id, customer_id, points, 'test-fixture'))
    conn.execute("UPDATE customers SET loyalty_points=? WHERE id=?", (points, customer_id))
    conn.commit()
    conn.close()


def _apply_ledger_and_cache_delta(company_id, customer_id, delta, entry_type='adjust'):
    """Writes a ledger row AND applies the identical delta to the
    `customers.loyalty_points` cache in the same call -- mirrors what every
    real mutation (create_sale's earn/redeem write, and this feature's own
    return reversal) always does to both columns together. Used to simulate
    a balance already moved by some OTHER, out-of-scope operation (e.g. a
    separate sale's redemption) before the return under test runs, without
    leaving the two columns artificially out of sync by construction."""
    conn = sch.get_retail_conn()
    conn.execute(
        "INSERT INTO loyalty_ledger (uid,company_id,customer_id,points_delta,entry_type,created_by) "
        "VALUES (?,?,?,?,?,?)",
        (str(uuid.uuid4()), company_id, customer_id, delta, entry_type, 'test-fixture'))
    conn.execute("UPDATE customers SET loyalty_points=loyalty_points+? WHERE id=?", (delta, customer_id))
    conn.commit()
    conn.close()


def _ledger_balance(company_id, customer_id):
    conn = sch.get_retail_conn()
    row = conn.execute(
        "SELECT COALESCE(SUM(points_delta),0) AS bal FROM loyalty_ledger WHERE company_id=? AND customer_id=?",
        (company_id, customer_id)).fetchone()
    conn.close()
    return row['bal']


def _ledger_rows(company_id, customer_id):
    conn = sch.get_retail_conn()
    rows = conn.execute(
        "SELECT * FROM loyalty_ledger WHERE company_id=? AND customer_id=? ORDER BY id",
        (company_id, customer_id)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _adjust_rows(company_id, customer_id):
    """Only the rows this feature's reversal could have written -- see
    `_grant_ledger_points`'s own docstring for why 'adjust' is otherwise
    unused anywhere in this file's fixtures."""
    return [r for r in _ledger_rows(company_id, customer_id) if r['entry_type'] == 'adjust']


def _company_ledger_row_count(company_id):
    conn = sch.get_retail_conn()
    n = conn.execute(
        "SELECT COUNT(*) AS n FROM loyalty_ledger WHERE company_id=?", (company_id,)
    ).fetchone()['n']
    conn.close()
    return n


def _customer_cache_points(customer_id):
    conn = sch.get_retail_conn()
    row = conn.execute("SELECT loyalty_points FROM customers WHERE id=?", (customer_id,)).fetchone()
    conn.close()
    return row['loyalty_points']


# ── 1. Full return of a sale that redeemed points gives them back exactly ──

def test_full_return_of_sale_that_redeemed_points_returns_them_exactly(shop):
    cid, admin = shop
    # sell_price=5 keeps this sale's total under 10, so it earns ZERO points
    # of its own (create_sale's `int(total/10)`) -- isolates the redeemed
    # side from the earned side, the same technique
    # retail_loyalty_redemption_test.py's own case 5 uses.
    pid = _create_product(admin, sell_price=5.0)
    customer_id = _create_customer(admin)
    _set_loyalty_point_value(admin, 1)
    _seed_loyalty_balance(cid, customer_id, 10)

    r = _sale(admin, [{'product_id': pid, 'quantity': 1}],
              customer_id=customer_id, points_redeemed=3, amount_paid=999999)
    assert r.status_code == 200, r.get_json()
    sale = r.get_json()['data']
    assert sale['points_redeemed_amount'] == 3.0
    assert _ledger_balance(cid, customer_id) == 7  # 10 - 3

    rr = _return(admin, sale['id'], [{'product_id': pid, 'quantity': 1}])
    assert rr.status_code == 200, rr.get_json()

    assert _ledger_balance(cid, customer_id) == 10, \
        "redeemed points must come back EXACTLY on a full return of the sale that spent them"
    adjusts = _adjust_rows(cid, customer_id)
    assert len(adjusts) == 1, adjusts
    assert adjusts[0]['points_delta'] == 3, adjusts
    assert _customer_cache_points(customer_id) == 10


# ── 2. Full return of a sale that earned points claws them back exactly ────

def test_full_return_of_sale_that_earned_points_claws_them_back_exactly(shop):
    cid, admin = shop
    pid = _create_product(admin, sell_price=100.0)  # total=100 -> earns int(100/10)=10
    customer_id = _create_customer(admin)

    r = _sale(admin, [{'product_id': pid, 'quantity': 1}], customer_id=customer_id, amount_paid=999999)
    assert r.status_code == 200, r.get_json()
    sale = r.get_json()['data']
    assert _ledger_balance(cid, customer_id) == 10

    rr = _return(admin, sale['id'], [{'product_id': pid, 'quantity': 1}])
    assert rr.status_code == 200, rr.get_json()

    assert _ledger_balance(cid, customer_id) == 0, \
        "earned points must be clawed back EXACTLY on a full return of the sale that earned them"
    adjusts = _adjust_rows(cid, customer_id)
    assert len(adjusts) == 1, adjusts
    assert adjusts[0]['points_delta'] == -10, adjusts
    assert _customer_cache_points(customer_id) == 0


# ── 3. Partial return settles both reversals proportionally on VALUE ───────

def test_partial_return_settles_proportionally_on_value_not_line_count(shop):
    """Also this file's differential proof against a line-count regression:
    two lines of DIFFERENT value (100 and 300) mean a value-based split
    (25%, since only the 100 line is returned out of a 400 total) and a
    line-count-based split (50%, one of two lines) produce DIFFERENT
    numbers. A regression to counting lines instead of value flips the
    `deltas` assertion below red on its own."""
    cid, admin = shop
    cheap_pid = _create_product(admin, sell_price=100.0)
    pricey_pid = _create_product(admin, sell_price=300.0)
    customer_id = _create_customer(admin)
    _set_loyalty_point_value(admin, 1)
    _seed_loyalty_balance(cid, customer_id, 60)

    r = _sale(admin, [{'product_id': cheap_pid, 'quantity': 1}, {'product_id': pricey_pid, 'quantity': 1}],
              customer_id=customer_id, points_redeemed=20, amount_paid=999999)
    assert r.status_code == 200, r.get_json()
    sale = r.get_json()['data']
    assert sale['total'] == 400.0
    # earn = int(400/10) = 40 on the sale's FULL total (unaffected by the
    # redemption -- create_sale's own comment); redeem = 20 (point_value=1,
    # $20 spent). Ledger after the sale, by hand: 60 - 20 (redeem) + 40
    # (earn) = 80.
    assert _ledger_balance(cid, customer_id) == 80

    # Return ONLY the cheap line: $100 of the sale's $400 total -- 25% of
    # the VALUE, 50% of the LINE COUNT.
    rr = _return(admin, sale['id'], [{'product_id': cheap_pid, 'quantity': 1}])
    assert rr.status_code == 200, rr.get_json()
    assert rr.get_json()['data']['refund_amount'] == 100.0

    adjusts = _adjust_rows(cid, customer_id)
    assert len(adjusts) == 2, adjusts
    deltas = sorted(a['points_delta'] for a in adjusts)
    # 25% of 40 earned = 10 clawed back (-10); 25% of 20 redeemed = 5 given
    # back (+5). A line-count-based (50%) regression would instead produce
    # [-20.0, 10.0].
    assert deltas == [-10.0, 5.0], (
        "must settle on the RETURNED VALUE (25%), not the line count (50%)", adjusts)

    # Requirement 7: the ledger balance after the return equals what you
    # would compute by hand: 80 - 10 (earn clawback) + 5 (redeem giveback) = 75.
    assert _ledger_balance(cid, customer_id) == 75
    assert _customer_cache_points(customer_id) == 75


# ── 4. A claw-back may drive the balance negative -- never clamped ─────────

def test_earn_clawback_can_drive_balance_negative_not_clamped(shop):
    cid, admin = shop
    pid = _create_product(admin, sell_price=100.0)  # earns 10
    customer_id = _create_customer(admin)

    r = _sale(admin, [{'product_id': pid, 'quantity': 1}], customer_id=customer_id, amount_paid=999999)
    assert r.status_code == 200, r.get_json()
    sale = r.get_json()['data']
    assert _ledger_balance(cid, customer_id) == 10

    # Simulate the customer having since spent that balance elsewhere (a
    # separate sale's redemption, out of scope here) -- drive the balance
    # to -90 BEFORE the return of the earning sale ever happens.
    _apply_ledger_and_cache_delta(cid, customer_id, -100)
    assert _ledger_balance(cid, customer_id) == -90

    rr = _return(admin, sale['id'], [{'product_id': pid, 'quantity': 1}])
    assert rr.status_code == 200, rr.get_json()

    # The full 10-point claw-back still applies in full: -90 - 10 = -100.
    # NOT clamped at 0 -- ROADMAP.md is explicit a deficit must be visible.
    assert _ledger_balance(cid, customer_id) == -100, \
        "a claw-back must be allowed to go negative -- it must never be clamped at zero"
    assert _customer_cache_points(customer_id) == -100, \
        "the customers.loyalty_points cache must track the (possibly negative) ledger sum exactly"


# ── 5. Returning a walk-in sale touches the ledger not at all ──────────────

def test_returning_walkin_sale_does_not_touch_ledger(shop):
    cid, admin = shop
    pid = _create_product(admin, sell_price=100.0)

    r = _sale(admin, [{'product_id': pid, 'quantity': 1}], amount_paid=999999)  # no customer_id: walk-in
    assert r.status_code == 200, r.get_json()
    sale = r.get_json()['data']
    assert _company_ledger_row_count(cid) == 0

    rr = _return(admin, sale['id'], [{'product_id': pid, 'quantity': 1}])
    assert rr.status_code == 200, rr.get_json()

    assert _company_ledger_row_count(cid) == 0, \
        "a walk-in sale earned and redeemed nothing, so its return must leave the ledger untouched"


# ── 6. Returning a sale with no ledger rows at all does not crash ──────────

def test_returning_sale_with_no_ledger_rows_does_not_crash(shop):
    """Reproduces the pre-v27 shape: a customer_id IS present on the sale,
    but the sale wrote ZERO loyalty_ledger rows -- exactly what a genuinely
    pre-feature sale looks like from create_return's point of view. A cheap
    product (total under 10, so create_sale's own `if pts:` guard never
    fires) reaches the identical COALESCE(...,0)/COALESCE(...,0) == 0/0
    path a hand-built legacy row would, without needing to bypass
    create_sale and hand-insert a `sales` row directly."""
    cid, admin = shop
    pid = _create_product(admin, sell_price=5.0)  # total=5 -> earn=int(5/10)=0; no redemption requested
    customer_id = _create_customer(admin)

    r = _sale(admin, [{'product_id': pid, 'quantity': 1}], customer_id=customer_id, amount_paid=999999)
    assert r.status_code == 200, r.get_json()
    sale = r.get_json()['data']
    assert _company_ledger_row_count(cid) == 0, "setup sanity: this sale must write no ledger row at all"

    rr = _return(admin, sale['id'], [{'product_id': pid, 'quantity': 1}])
    assert rr.status_code == 200, rr.get_json()

    assert _company_ledger_row_count(cid) == 0, \
        "no reversal may be invented where the original sale left no ledger entry to reverse"
