"""
Aura Retail -- P0 item #2 / interface I-10: owner-only company-wide writes
and money writes the branch-staff delegation toggle must not unlock.

THE BUG: the shipped 'Allow to Manage Branch Staff' toggle (employees.js)
grants a branch-scoped manager `retail.employees=full`. `mt_require_capability`
only ever reads that ONE row -- it has no way to know the toggle's own copy
promises "own-branch staff" and nothing wider. So that manager already passed
every `@mt_require_capability(CAP_EMPLOYEES)` route in the file: the shop's
tax mode, branding, document numbering, the trading day, payment methods,
creating branches, and every cheque/supplier-payment/PO-payment route --
none of which checks branch scope, because none of them is scoped BY branch
at all. Two verifiers found this under the redesign; this suite is the fix.

THE FIX: `_require_company_admin()` (already used by demo_wipe/demo_seed/
inventory_reconciliation) is now ALSO called, first line of the function
body, by every route interface I-10 names -- see its own docstring in
retail_api.py and the `EXPECTED_COMPANY_ADMIN_ROUTES` table in
retail_route_capability_matrix_test.py, which is the STATIC ratchet for
"which routes require the owner" (this file is the LIVE half: real users,
real HTTP, the real delegation grant).

Deliberately real HTTP throughout, never a raw DB write standing in for a
route: the manager's delegation is granted through the SAME
`POST /api/admin/employees/<id>/permissions` employees.js's
`_toggleDelegation` calls, and the branch scope through the same
`PUT .../branch-scope` its "Change Branch" control calls -- a fixture that
hand-wrote `user_permissions`/`branch_scope_uid` rows could pass this suite
while a bug in either GRANT route itself went uncaught.

`code: 'OWNER_ONLY'` on every refusal here is the fingerprint that THIS
check fired, not some other 403 already in front of it: the delegated
manager holds `retail.employees` (deliberately, that is the whole point of
the toggle), so before this fix every one of these calls returned 200 --
`mt_require_capability` never refused them. A bare 403 would not tell
"the owner check ran and refused" from "something else refused first";
asserting the exact code does.

Run (ONE FILE PER PROCESS -- this suite boots its own Flask app at import,
like every other *_test.py in this directory):
    pytest products/retail/tests/retail_p0_owner_guard_test.py -q
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_ownerguard_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402
seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")
os.environ.pop("AURA_RETAIL_DEMO_MODE", None)

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from commercial_runtime.identity import user_accounts  # noqa: E402
from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402
from database.schema import get_retail_conn  # noqa: E402

# Same module object app.py's init_app() actually registered -- needed to
# call `_default_branch` directly to self-heal a real branch row (never a
# hand-fabricated uid -- branch_scope_schema.py's own contract is that
# `branch_scope_uid` is NEVER validated against `branches` server-side,
# which is exactly why this suite must name a REAL one: a fabricated uid
# would still pass every route below and prove nothing about a manager
# scoped to an ACTUAL branch a shop could actually have).
import api.retail_api as retail_api_module  # noqa: E402


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


def _make_user(role, *, company_id=None, branch_scope_uid=None):
    """A real account, seeded through the same production helper account
    creation uses -- see retail_route_capability_matrix_test.py's identical
    `_make_user` for why a hand-rolled grant set would be the wrong fixture
    here too."""
    email = f"i10-{role}-{uuid.uuid4().hex[:10]}@test.local"
    password = "OwnerGuardPW1"
    company_id = company_id or str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, "
        "require_password_change, branch_scope_uid) VALUES (?,?,?,?,?,?,?,0,?)",
        (user_id, company_id, f"EMP-{uuid.uuid4().hex[:6]}", email, hash_password(password),
         role, "active", branch_scope_uid),
    )
    # mt_require_subsystem still demands the legacy un-namespaced grant, so
    # without this the request never reaches the capability check at all.
    conn.execute(
        "INSERT INTO user_permissions (id, user_id, subsystem, access_level) VALUES (?,?,?,?)",
        (str(uuid.uuid4()), user_id, "retail", "full"),
    )
    user_accounts.seed_capabilities_for_user(conn, user_id, role)
    conn.commit()
    conn.close()

    client = app.test_client()
    r = client.post('/api/auth/login', json={'email': email, 'password': password})
    assert r.status_code == 200, r.get_json()
    return client, company_id, user_id, email, password


def _cheque_count(company_id):
    conn = get_retail_conn()
    n = conn.execute("SELECT COUNT(*) FROM cheques WHERE company_id=?", (company_id,)).fetchone()[0]
    conn.close()
    return n


def _owner_and_delegated_manager():
    """P0 round 2 (item B5): factored out of `shop` below, byte-identical
    behaviour -- `shop` was the only caller until the demo-mode fixture
    needed the SAME real delegation dance (owner, self-healed branch,
    manager scoped to it, the branch-staff toggle granted, the manager's
    forced re-login) on a SEPARATE, isolated company. Extracting it here
    means `demo_shop` below gets the exact same real-HTTP grant path this
    file's own module docstring insists on, not a second hand-rolled copy
    that could quietly drift from it.

    Returns (owner_client, manager_client, company_id, manager_id, branch_uid).
    """
    owner, company_id, owner_id, _owner_email, _owner_pw = _make_user('admin')
    # Runs _ensure_credit_schema / doc_sequences the same way
    # retail_route_capability_matrix_test.py's _seed_shop does -- every
    # route below (tax/branding/doc-series/cheques) reads tables that
    # migration creates.
    owner.get('/api/sub/retail/settings/tax')

    conn = get_retail_conn()
    branch_id = retail_api_module._default_branch(conn, company_id)
    branch_uid = conn.execute("SELECT uid FROM branches WHERE id=?", (branch_id,)).fetchone()['uid']
    conn.commit()
    conn.close()

    manager, _, manager_id, manager_email, manager_pw = _make_user('manager', company_id=company_id)

    # Scope the manager to that real branch -- PUT .../branch-scope, the
    # exact route employees.js's "Change Branch" control calls.
    r = owner.put(f'/api/admin/employees/{manager_id}/branch-scope',
                  json={'branch_scope_uid': branch_uid})
    assert r.status_code == 200, r.get_json()

    # Grant "Allow to Manage Branch Staff" -- EXACTLY the POST employees.js's
    # _toggleDelegation sends (item #2's own instruction). Before this
    # suite's fix, this one grant alone was enough to pass every route
    # tested below.
    r = owner.post(f'/api/admin/employees/{manager_id}/permissions',
                    json={'subsystem': 'retail.employees', 'access_level': 'full'})
    assert r.status_code == 200, r.get_json()

    # Both grants above bump the manager's `session_version` (registry v7 --
    # update_branch_scope's/update_perms' own G6 comment: "a session already
    # open on this account must re-read its own row"), so the manager's
    # ORIGINAL login cookie is now dead-on-arrival -- measured: it came back
    # 401 "Session expired or revoked" before this re-login was added, which
    # is the security mechanism working correctly, not a fixture bug to
    # route around. This is exactly what a real branch manager would do
    # after the owner turns the toggle on for them: sign in again.
    r = manager.post('/api/auth/login', json={'email': manager_email, 'password': manager_pw})
    assert r.status_code == 200, r.get_json()

    return owner, manager, company_id, manager_id, branch_uid


@pytest.fixture(scope='module')
def shop():
    """One company: an owner, a real self-healed branch, a product, a
    supplier, an unpaid PO, and a manager DELEGATED exactly the way
    employees.js grants it -- see the module docstring above for why both
    grant routes (branch-scope, permissions) run for real rather than being
    faked with a direct INSERT.
    """
    owner, manager, company_id, manager_id, branch_uid = _owner_and_delegated_manager()

    r = owner.post('/api/sub/retail/products', json={
        'name': 'I-10 Test Product', 'sku': f'I10-{uuid.uuid4().hex[:8]}',
        'sell_price': 50.0, 'tax_rate': 0, 'initial_stock': 100,
    })
    assert r.status_code == 200, r.get_json()
    product_id = r.get_json()['data']['id']

    r = owner.post('/api/sub/retail/suppliers', json={'name': f'I-10 Supplier {uuid.uuid4().hex[:6]}'})
    assert r.status_code == 200, r.get_json()
    supplier_id = r.get_json()['data']['id']

    r = owner.post('/api/sub/retail/customers', json={'name': f'I-10 Customer {uuid.uuid4().hex[:6]}'})
    assert r.status_code == 200, r.get_json()
    customer_id = r.get_json()['data']['id']

    # An unpaid PO -- create_purchase_order itself is CAP_STOCK, unaffected
    # by I-10 (a manager may still ORDER stock); only PAYING it is
    # owner-only, which is exactly what this fixture exists to exercise.
    r = owner.post('/api/sub/retail/purchase-orders', json={
        'supplier_id': supplier_id, 'amount_paid': 0,
        'items': [{'product_id': product_id, 'quantity': 1, 'unit_cost': 10}],
    })
    assert r.status_code == 200, r.get_json()
    po_id = r.get_json()['data']['id']

    return {
        'owner': owner, 'manager': manager, 'company_id': company_id,
        'manager_id': manager_id, 'branch_uid': branch_uid,
        'product_id': product_id, 'supplier_id': supplier_id,
        'customer_id': customer_id, 'po_id': po_id,
    }


@pytest.fixture
def demo_shop(monkeypatch):
    """P0 round 2 (item B5): `demo_wipe`/`demo_seed` DELETE-then-reseed (or
    just DELETE) the caller's ENTIRE company -- every product, branch,
    customer, supplier, sale, PO, audit row. Sharing the module-scoped
    `shop` fixture above would mean whichever of these tests happens to run
    first destroys the fixture every OTHER test in this file depends on
    (pytest runs a file's tests in definition order by default, but nothing
    enforces that, and a future reordering or `-k` selection must not be
    able to turn "prove demo-wipe is owner-gated" into "silently break 20
    unrelated tests"). Function-scoped and its OWN isolated company for
    exactly that reason -- matches `test_a_four_item_sale_names_every_
    line_and_none_is_the_bare_id`'s identical "independent of the shared
    fixture" posture in retail_p0_backend_test.py.

    `demo_wipe`/`demo_seed` ALSO gate on `_require_retail_demo_mode()`
    BEFORE `_require_company_admin()` (see each route's own `for guard in
    (...)` line) -- real builds refuse both regardless of who is asking, so
    proving the OWNER-ONLY check specifically fires needs demo mode ON.
    `retail_demo_mode_enabled()` reads `os.environ.get('AURA_RETAIL_DEMO_
    MODE')` at CALL time (commercial_runtime/security/modes.py), so
    `monkeypatch.setenv` here is scoped to exactly this fixture's tests and
    reverts automatically -- this file's own bootstrap explicitly `pop()`s
    that variable so ordinary tests never run in demo mode by accident.
    """
    monkeypatch.setenv('AURA_RETAIL_DEMO_MODE', '1')
    owner, manager, company_id, manager_id, branch_uid = _owner_and_delegated_manager()
    return {'owner': owner, 'manager': manager, 'company_id': company_id}


# ── The deny half: the delegated manager, on every I-10 route ───────────────

def _tax_mode(company_id):
    conn = get_retail_conn()
    row = conn.execute(
        "SELECT svalue FROM retail_settings WHERE company_id=? AND skey='tax_calculation_mode'",
        (company_id,),
    ).fetchone()
    conn.close()
    return row['svalue'] if row else None


def test_delegated_manager_denied_owner_only_on_tax_settings(shop):
    # Reviewer finding (P0 item #2, "test coverage"): this assertion used to
    # read `_cheque_count` before/after -- the WRONG table, which can only
    # ever prove "this route did not also write a cheque", never "this route
    # did not write the tax mode". Reading the actual column this route
    # writes is what makes the "no side effect" half of the claim true.
    before = _tax_mode(shop['company_id'])
    r = shop['manager'].post('/api/sub/retail/settings/tax', json={'tax_calculation_mode': 'after_discount'})
    assert r.status_code == 403, r.get_json()
    assert r.get_json().get('code') == 'OWNER_ONLY', r.get_json()
    assert _tax_mode(shop['company_id']) == before  # no side effect leaked through


def test_delegated_manager_denied_owner_only_on_branding(shop):
    r = shop['manager'].post('/api/sub/retail/settings/branding', json={'branding_business_name': 'Hijacked Co'})
    assert r.status_code == 403, r.get_json()
    assert r.get_json().get('code') == 'OWNER_ONLY', r.get_json()
    # And the write really did not happen -- not just "some 403 arrived".
    conn = get_retail_conn()
    row = conn.execute(
        "SELECT svalue FROM retail_settings WHERE company_id=? AND skey='branding_business_name'",
        (shop['company_id'],)
    ).fetchone()
    conn.close()
    assert row is None or row['svalue'] != 'Hijacked Co'


def test_delegated_manager_denied_owner_only_on_doc_numbering(shop):
    r = shop['manager'].post('/api/sub/retail/doc-series', json={'doc_type': 'po', 'code': 'MGRPO'})
    assert r.status_code == 403, r.get_json()
    assert r.get_json().get('code') == 'OWNER_ONLY', r.get_json()
    conn = get_retail_conn()
    row = conn.execute(
        "SELECT 1 FROM doc_series WHERE company_id=? AND code='MGRPO'", (shop['company_id'],)
    ).fetchone()
    conn.close()
    assert row is None, "a denied manager's doc-series request was written anyway"


def test_delegated_manager_denied_owner_only_on_cheque_write(shop):
    before = _cheque_count(shop['company_id'])
    r = shop['manager'].post('/api/sub/retail/cheques', json={
        'direction': 'in', 'party_id': shop['customer_id'],
        'amount': 25, 'due_date': '2027-01-15', 'cheque_number': f'MGR-{uuid.uuid4().hex[:6]}',
    })
    assert r.status_code == 403, r.get_json()
    assert r.get_json().get('code') == 'OWNER_ONLY', r.get_json()
    assert _cheque_count(shop['company_id']) == before, (
        "a denied manager's cheque write was recorded anyway -- the guard "
        "must run before the INSERT, not after"
    )


def test_delegated_manager_denied_owner_only_on_po_pay(shop):
    r = shop['manager'].post(f'/api/sub/retail/purchase-orders/{shop["po_id"]}/pay', json={'amount': 5})
    assert r.status_code == 403, r.get_json()
    assert r.get_json().get('code') == 'OWNER_ONLY', r.get_json()
    conn = get_retail_conn()
    row = conn.execute(
        "SELECT COALESCE(amount_paid,0) AS amount_paid FROM purchase_orders WHERE id=? AND company_id=?",
        (shop['po_id'], shop['company_id'])
    ).fetchone()
    conn.close()
    assert row['amount_paid'] == 0, "a denied manager's PO payment posted anyway"


# ── The allow half: the SAME delegated manager, on what the toggle actually
#    promises -- own-branch staff management ────────────────────────────────

def test_delegated_manager_gets_200_inviting_own_branch_cashier(shop):
    email = f"i10-invited-cashier-{uuid.uuid4().hex[:8]}@test.local"
    r = shop['manager'].post('/api/admin/employees', json={'email': email, 'role': 'cashier'})
    assert r.status_code == 200, r.get_json()
    assert r.get_json().get('success') is True, r.get_json()

    # And it landed scoped to the MANAGER's own branch (G3 stamping,
    # onboarding_routes.py) -- the toggle's whole promise, still intact
    # after I-10.
    conn = registry_conn()
    row = conn.execute(
        "SELECT role, branch_scope_uid FROM users WHERE company_id=? AND email=?",
        (shop['company_id'], email)
    ).fetchone()
    conn.close()
    assert row is not None, "the delegated invite was accepted (200) but no row was written"
    assert row['role'] == 'cashier'
    assert row['branch_scope_uid'] == shop['branch_uid']


# ── The allow half: the owner, on every I-10 route ───────────────────────────

def test_owner_gets_200_on_tax_settings(shop):
    r = shop['owner'].post('/api/sub/retail/settings/tax', json={'tax_calculation_mode': 'before_discount'})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['data']['tax_calculation_mode'] == 'before_discount'


def test_owner_gets_200_on_branding(shop):
    r = shop['owner'].post('/api/sub/retail/settings/branding', json={'branding_business_name': 'Owner Co'})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['data']['branding_business_name'] == 'Owner Co'


def test_owner_gets_200_on_doc_numbering(shop):
    r = shop['owner'].post('/api/sub/retail/doc-series', json={'doc_type': 'po', 'code': 'OWNPO'})
    assert r.status_code == 200, r.get_json()


def test_owner_gets_200_on_cheque_write(shop):
    before = _cheque_count(shop['company_id'])
    r = shop['owner'].post('/api/sub/retail/cheques', json={
        'direction': 'in', 'party_id': shop['customer_id'],
        'amount': 25, 'due_date': '2027-01-15', 'cheque_number': f'OWN-{uuid.uuid4().hex[:6]}',
    })
    assert r.status_code == 201, r.get_json()
    assert _cheque_count(shop['company_id']) == before + 1


def test_owner_gets_200_on_po_pay(shop):
    r = shop['owner'].post(f'/api/sub/retail/purchase-orders/{shop["po_id"]}/pay', json={'amount': 5})
    assert r.status_code == 200, r.get_json()
    conn = get_retail_conn()
    row = conn.execute(
        "SELECT COALESCE(amount_paid,0) AS amount_paid FROM purchase_orders WHERE id=? AND company_id=?",
        (shop['po_id'], shop['company_id'])
    ).fetchone()
    conn.close()
    assert row['amount_paid'] == 5


# ── Reviewer pass on the I-10 rollout above (retail-hardware-viewports,
# second C2 dispatch): every route below was found still reachable through
# CAP_EMPLOYEES alone despite doing the same class of company-wide/money
# write as the routes already proven above. Each test proves BOTH the deny
# half (delegated manager, 403 OWNER_ONLY, state unchanged) and, where the
# route has one, the allow half it must not have broken (owner 200, or -- for
# create_purchase_order/void_payment -- the specific ALLOWED sibling action
# the fix must not have swept up with it).

def _po_amount_paid(company_id, po_id):
    conn = get_retail_conn()
    row = conn.execute(
        "SELECT COALESCE(amount_paid,0) AS amount_paid FROM purchase_orders WHERE id=? AND company_id=?",
        (po_id, company_id),
    ).fetchone()
    conn.close()
    return row['amount_paid']


def _active_supplier_payment_count(company_id):
    conn = get_retail_conn()
    n = conn.execute(
        "SELECT COUNT(*) FROM payments WHERE company_id=? AND party_type='supplier' AND status='active'",
        (company_id,),
    ).fetchone()[0]
    conn.close()
    return n


def test_delegated_manager_denied_owner_only_on_po_amount_paid(shop):
    """#2 (I-10) reviewer finding: create_purchase_order's OWN amount_paid
    field is a second, weaker door to the exact money-out action
    supplier_payment/pay_purchase_order are already gated for above --
    before this fix, a delegated manager refused by both dedicated payment
    routes could still pay a supplier by typing a number into a brand-new
    PO's amount_paid instead."""
    supplier_payments_before = _active_supplier_payment_count(shop['company_id'])
    r = shop['manager'].post('/api/sub/retail/purchase-orders', json={
        'supplier_id': shop['supplier_id'], 'amount_paid': 20,
        'items': [{'product_id': shop['product_id'], 'quantity': 1, 'unit_cost': 10}],
    })
    assert r.status_code == 403, r.get_json()
    assert r.get_json().get('code') == 'OWNER_ONLY', r.get_json()
    # Refuses the WHOLE request (create_purchase_order's own comment), not
    # just the payment half -- no PO and no payment row landed.
    assert _active_supplier_payment_count(shop['company_id']) == supplier_payments_before, (
        "a denied manager's PO amount_paid posted a supplier payment anyway"
    )


def test_delegated_manager_gets_200_on_po_with_zero_amount_paid(shop):
    """The allow half of the fix above: amount_paid=0 (ordering/receiving
    stock, no money moved) is a manager action and must still succeed -- the
    fix must gate only the money-out branch, not the whole route."""
    r = shop['manager'].post('/api/sub/retail/purchase-orders', json={
        'supplier_id': shop['supplier_id'], 'amount_paid': 0,
        'items': [{'product_id': shop['product_id'], 'quantity': 1, 'unit_cost': 10}],
    })
    assert r.status_code == 200, r.get_json()
    assert _po_amount_paid(shop['company_id'], r.get_json()['data']['id']) == 0


def test_delegated_manager_denied_owner_only_on_supplier_payment_void(shop):
    """#2 (I-10) reviewer finding: the authority to REVERSE a supplier
    payment must equal the authority to create one -- supplier_payment is
    owner-only, so voiding one must be too, or a delegated manager could
    reopen a payable the owner had just settled."""
    r = shop['owner'].post(f'/api/sub/retail/suppliers/{shop["supplier_id"]}/payments',
                            json={'amount': 7, 'method': 'cash'})
    assert r.status_code == 200, r.get_json()
    payment_id = r.get_json()['data']['reference']
    conn = get_retail_conn()
    row = conn.execute(
        "SELECT id, status FROM payments WHERE company_id=? AND reference=?",
        (shop['company_id'], payment_id),
    ).fetchone()
    conn.close()
    pid = row['id']

    r = shop['manager'].post(f'/api/sub/retail/payments/{pid}/void', json={'reason': 'test'})
    assert r.status_code == 403, r.get_json()
    assert r.get_json().get('code') == 'OWNER_ONLY', r.get_json()

    conn = get_retail_conn()
    status_after = conn.execute("SELECT status FROM payments WHERE id=?", (pid,)).fetchone()['status']
    conn.close()
    assert status_after == 'active', "a denied manager's void changed the payment's status anyway"


def test_delegated_manager_gets_200_voiding_a_customer_receipt(shop):
    """The allow half: party_type='customer' stays on CAP_REFUND (its own
    pre-existing, unrelated authority) -- the supplier-only fix above must
    not have swept up the till's own receipt-correction path."""
    r = shop['owner'].post(f'/api/sub/retail/customers/{shop["customer_id"]}/payments',
                            json={'amount': 3, 'method': 'cash'})
    assert r.status_code == 200, r.get_json()
    payment_id = r.get_json()['data']['reference']
    conn = get_retail_conn()
    pid = conn.execute(
        "SELECT id FROM payments WHERE company_id=? AND reference=?",
        (shop['company_id'], payment_id),
    ).fetchone()['id']
    conn.close()

    r = shop['manager'].post(f'/api/sub/retail/payments/{pid}/void', json={'reason': 'test'})
    assert r.status_code == 200, r.get_json()


def test_owner_gets_200_voiding_a_supplier_payment(shop):
    r = shop['owner'].post(f'/api/sub/retail/suppliers/{shop["supplier_id"]}/payments',
                            json={'amount': 4, 'method': 'cash'})
    assert r.status_code == 200, r.get_json()
    payment_id = r.get_json()['data']['reference']
    conn = get_retail_conn()
    pid = conn.execute(
        "SELECT id FROM payments WHERE company_id=? AND reference=?",
        (shop['company_id'], payment_id),
    ).fetchone()['id']
    conn.close()

    r = shop['owner'].post(f'/api/sub/retail/payments/{pid}/void', json={'reason': 'test'})
    assert r.status_code == 200, r.get_json()


def _base_currency(company_id):
    conn = get_retail_conn()
    row = conn.execute(
        "SELECT svalue FROM retail_settings WHERE company_id=? AND skey='base_currency'",
        (company_id,),
    ).fetchone()
    conn.close()
    return row['svalue'] if row else None


def test_delegated_manager_denied_owner_only_on_credit_settings(shop):
    """#2 (I-10) reviewer finding: credit_settings_set writes base_currency
    alongside the credit-policy and loyalty fields -- the single most
    disruptive company-wide setting in this file (every historical money
    display/report reads it), previously reachable through CAP_EMPLOYEES
    alone."""
    before = _base_currency(shop['company_id'])
    r = shop['manager'].post('/api/sub/retail/settings/credit', json={'base_currency': 'USD'})
    assert r.status_code == 403, r.get_json()
    assert r.get_json().get('code') == 'OWNER_ONLY', r.get_json()
    assert _base_currency(shop['company_id']) == before


def test_owner_gets_200_on_credit_settings(shop):
    r = shop['owner'].post('/api/sub/retail/settings/credit', json={'enforce_credit_limit': '1'})
    assert r.status_code == 200, r.get_json()


def test_delegated_manager_denied_owner_only_on_doc_series_update_and_retire(shop):
    """#2 (I-10) reviewer finding: create_doc_series was already owner-only;
    its PATCH/DELETE siblings were not, so a delegated manager could relabel
    or retire ANY series in the company, including the owner's."""
    r = shop['owner'].post('/api/sub/retail/doc-series', json={'doc_type': 'sale', 'code': f'I10-{uuid.uuid4().hex[:6]}'})
    assert r.status_code == 200, r.get_json()
    series_id = r.get_json()['data']['id']

    r = shop['manager'].patch(f'/api/sub/retail/doc-series/{series_id}', json={'label': 'Hijacked'})
    assert r.status_code == 403, r.get_json()
    assert r.get_json().get('code') == 'OWNER_ONLY', r.get_json()

    r = shop['manager'].delete(f'/api/sub/retail/doc-series/{series_id}')
    assert r.status_code == 403, r.get_json()
    assert r.get_json().get('code') == 'OWNER_ONLY', r.get_json()

    conn = get_retail_conn()
    row = conn.execute("SELECT label, status FROM doc_series WHERE id=?", (series_id,)).fetchone()
    conn.close()
    assert row['label'] != 'Hijacked'
    assert row['status'] == 'active', "a denied manager's retire changed the series' status anyway"


def test_owner_gets_200_on_doc_series_update_and_retire(shop):
    r = shop['owner'].post('/api/sub/retail/doc-series', json={'doc_type': 'return', 'code': f'I10-{uuid.uuid4().hex[:6]}'})
    assert r.status_code == 200, r.get_json()
    series_id = r.get_json()['data']['id']

    r = shop['owner'].patch(f'/api/sub/retail/doc-series/{series_id}', json={'label': 'Owner Relabel'})
    assert r.status_code == 200, r.get_json()

    r = shop['owner'].delete(f'/api/sub/retail/doc-series/{series_id}')
    assert r.status_code == 200, r.get_json()

    conn = get_retail_conn()
    row = conn.execute("SELECT label, status FROM doc_series WHERE id=?", (series_id,)).fetchone()
    conn.close()
    assert row['label'] == 'Owner Relabel'
    assert row['status'] == 'retired'


def _has_logo(company_id):
    # `_BRANDING_LOGO_KEY` (retail_api.py) is `_SETTINGS_BLOB_PREFIX +
    # 'branding_logo'` == `'blob_branding_logo'`, never the bare
    # `'branding_logo'` -- read through the real module constant rather than
    # re-typing the literal, so a future rename of the prefix cannot make
    # this helper quietly stop finding the row it exists to check.
    conn = get_retail_conn()
    row = conn.execute(
        "SELECT svalue FROM retail_settings WHERE company_id=? AND skey=?",
        (company_id, retail_api_module._BRANDING_LOGO_KEY),
    ).fetchone()
    conn.close()
    return bool(row and row['svalue'])


_TEST_LOGO_DATA_URI = (
    'data:image/png;base64,'
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII='
)


def test_delegated_manager_denied_owner_only_on_branding_logo(shop):
    """#2 (I-10) / I-10-rollout reviewer finding: branding_settings_set (the
    text fields) was already owner-only; the logo upload/erase siblings were
    not, despite branding_settings_set's own comment calling the logo
    company-wide identity too."""
    assert _has_logo(shop['company_id']) is False
    r = shop['manager'].post('/api/sub/retail/settings/branding/logo', json={'logo': _TEST_LOGO_DATA_URI})
    assert r.status_code == 403, r.get_json()
    assert r.get_json().get('code') == 'OWNER_ONLY', r.get_json()
    assert _has_logo(shop['company_id']) is False

    # Seed a real logo as the owner, then prove the manager cannot erase it.
    r = shop['owner'].post('/api/sub/retail/settings/branding/logo', json={'logo': _TEST_LOGO_DATA_URI})
    assert r.status_code == 200, r.get_json()
    assert _has_logo(shop['company_id']) is True

    r = shop['manager'].delete('/api/sub/retail/settings/branding/logo')
    assert r.status_code == 403, r.get_json()
    assert r.get_json().get('code') == 'OWNER_ONLY', r.get_json()
    assert _has_logo(shop['company_id']) is True, "a denied manager's logo delete erased it anyway"


def test_owner_gets_200_on_branding_logo_set_and_delete(shop):
    r = shop['owner'].post('/api/sub/retail/settings/branding/logo', json={'logo': _TEST_LOGO_DATA_URI})
    assert r.status_code == 200, r.get_json()
    assert _has_logo(shop['company_id']) is True

    r = shop['owner'].delete('/api/sub/retail/settings/branding/logo')
    assert r.status_code == 200, r.get_json()
    assert _has_logo(shop['company_id']) is False


def _branch_name(branch_id):
    conn = get_retail_conn()
    row = conn.execute("SELECT name, status FROM branches WHERE id=?", (branch_id,)).fetchone()
    conn.close()
    return row['name'], row['status']


def test_delegated_manager_denied_owner_only_on_branch_update_and_retire(shop):
    """#2 (I-10) reviewer finding: create_branch was already owner-only; its
    update/retire siblings were not, so a delegated manager scoped to their
    OWN branch could still rename or retire ANOTHER branch in the company --
    reshaping the company, not "managing my own branch's staff"."""
    r = shop['owner'].post('/api/sub/retail/branches', json={'name': f'I10 Second Branch {uuid.uuid4().hex[:6]}'})
    assert r.status_code == 200, r.get_json()
    other_branch_id = r.get_json()['data']['id']
    name_before, status_before = _branch_name(other_branch_id)

    r = shop['manager'].put(f'/api/sub/retail/branches/{other_branch_id}', json={'name': 'Renamed By Manager'})
    assert r.status_code == 403, r.get_json()
    assert r.get_json().get('code') == 'OWNER_ONLY', r.get_json()

    r = shop['manager'].post(f'/api/sub/retail/branches/{other_branch_id}/retire')
    assert r.status_code == 403, r.get_json()
    assert r.get_json().get('code') == 'OWNER_ONLY', r.get_json()

    name_after, status_after = _branch_name(other_branch_id)
    assert name_after == name_before, "a denied manager's rename landed anyway"
    assert status_after == status_before, "a denied manager's retire landed anyway"


def test_owner_gets_200_on_branch_update_and_retire(shop):
    r = shop['owner'].post('/api/sub/retail/branches', json={'name': f'I10 Third Branch {uuid.uuid4().hex[:6]}'})
    assert r.status_code == 200, r.get_json()
    branch_id = r.get_json()['data']['id']

    r = shop['owner'].put(f'/api/sub/retail/branches/{branch_id}', json={'name': 'Owner Renamed'})
    assert r.status_code == 200, r.get_json()

    r = shop['owner'].post(f'/api/sub/retail/branches/{branch_id}/retire')
    assert r.status_code == 200, r.get_json()

    name_after, status_after = _branch_name(branch_id)
    assert name_after == 'Owner Renamed'
    # retire_branch writes status='inactive' (retail_api.py:12090), not
    # 'retired' -- matches _resolve_working_branch/_default_branch/
    # list_branches' own `COALESCE(status,'active')='active'` filter, which
    # is what actually makes a retired branch stop being selectable.
    assert status_after == 'inactive'


# ═════════════════════════════════════════════════════════════════════════
# P0 round 2 (item B5): "re-confirm every EXPECTED_COMPANY_ADMIN_ROUTES
# entry has a LIVE deny and allow test; add any missing." The routes below
# were all correctly gated by C1's original I-10 pass and are all already
# in retail_route_capability_matrix_test.py's EXPECTED_COMPANY_ADMIN_ROUTES
# -- what was missing was a LIVE proof for each. Grouped by the money/entity
# family the routes belong to, matching this file's own existing layout.
# ═════════════════════════════════════════════════════════════════════════

def test_delegated_manager_denied_owner_only_on_create_branch(shop):
    """`create_branch` itself was always exercised as the OWNER (setting up
    the branches the update/retire tests above rename and retire), but
    never actually POSTed as the delegated manager -- the one entry in
    EXPECTED_COMPANY_ADMIN_ROUTES' branch group with no deny test of its
    own until now."""
    before = _branch_count(shop['company_id'])
    r = shop['manager'].post('/api/sub/retail/branches', json={'name': f'Manager Branch {uuid.uuid4().hex[:6]}'})
    assert r.status_code == 403, r.get_json()
    assert r.get_json().get('code') == 'OWNER_ONLY', r.get_json()
    assert _branch_count(shop['company_id']) == before, "a denied manager's branch create landed anyway"


def _branch_count(company_id):
    conn = get_retail_conn()
    n = conn.execute("SELECT COUNT(*) FROM branches WHERE company_id=?", (company_id,)).fetchone()[0]
    conn.close()
    return n


def test_owner_gets_200_on_create_branch(shop):
    """The allow half, named explicitly rather than left implicit in the
    update/retire tests' own setup calls."""
    before = _branch_count(shop['company_id'])
    r = shop['owner'].post('/api/sub/retail/branches', json={'name': f'Owner Fourth Branch {uuid.uuid4().hex[:6]}'})
    assert r.status_code == 200, r.get_json()
    assert _branch_count(shop['company_id']) == before + 1


# ── Cheque lifecycle: create_cheque's own deny/allow tests are above
#    (test_delegated_manager_denied_owner_only_on_cheque_write /
#    test_owner_gets_200_on_cheque_write); the other seven state-transition
#    routes each carry the IDENTICAL `_require_company_admin()` gate (see
#    each one's own "see create_cheque's identical gate comment above" in
#    retail_api.py) but had no live proof of their own until now. ─────────

def _create_cheque(client_, *, direction, party_id, amount=25, cheque_number=None):
    r = client_.post('/api/sub/retail/cheques', json={
        'direction': direction, 'party_id': party_id, 'amount': amount,
        'due_date': '2027-01-15', 'cheque_number': cheque_number or f'CHQ-{uuid.uuid4().hex[:8]}',
    })
    assert r.status_code == 201, r.get_json()
    return r.get_json()['data']['id']


def _cheque_status(owner_client, cheque_id):
    # `cheques` carries no `status` COLUMN -- it is folded from
    # `cheque_events` (core/retail/cheques.py's `fold()`) -- so reading it
    # back means the real GET route, the same one the Cheques screen reads,
    # not a raw SELECT this file would have to keep in sync with the fold
    # logic by hand.
    r = owner_client.get(f'/api/sub/retail/cheques/{cheque_id}')
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['cheque']['status']


def test_delegated_manager_denied_owner_only_on_cheque_lifecycle_actions(shop):
    """The admin guard is the FIRST line of every one of these seven route
    bodies, before the cheque lookup or any transition-legality check (see
    e.g. `deposit_cheque`'s own body in retail_api.py) -- so a single
    PENDING cheque proves all seven at once: a 403 here can never be "wrong
    state for that action", it can only be the owner check firing. State is
    re-read after EVERY action, not just at the end, so an action that
    slipped through would be caught by the very next one's "still pending"
    assertion even if it changed the status to something a later action's
    own precondition would also accept."""
    cheque_id = _create_cheque(shop['owner'], direction='in', party_id=shop['customer_id'])
    assert _cheque_status(shop['owner'], cheque_id) == 'pending'

    actions = [
        ('deposit', {}),
        ('clear', {}),
        ('bounce', {'reason': 'insufficient funds'}),
        ('reinstate', {}),
        ('endorse', {'to_party_type': 'supplier', 'to_party_id': shop['supplier_id']}),
        ('cancel', {}),
        ('write-off', {}),
    ]
    for action, body in actions:
        r = shop['manager'].post(f'/api/sub/retail/cheques/{cheque_id}/{action}', json=body)
        assert r.status_code == 403, (action, r.get_json())
        assert r.get_json().get('code') == 'OWNER_ONLY', (action, r.get_json())
        assert _cheque_status(shop['owner'], cheque_id) == 'pending', (
            f"a denied manager's {action!r} changed the cheque's status anyway")


def test_owner_gets_200_on_cheque_deposit_and_clear(shop):
    cheque_id = _create_cheque(shop['owner'], direction='in', party_id=shop['customer_id'])
    r = shop['owner'].post(f'/api/sub/retail/cheques/{cheque_id}/deposit', json={})
    assert r.status_code == 200, r.get_json()
    assert _cheque_status(shop['owner'], cheque_id) == 'deposited'

    r = shop['owner'].post(f'/api/sub/retail/cheques/{cheque_id}/clear', json={})
    assert r.status_code == 200, r.get_json()
    assert _cheque_status(shop['owner'], cheque_id) == 'cleared'


def test_owner_gets_200_on_cheque_bounce_and_reinstate(shop):
    cheque_id = _create_cheque(shop['owner'], direction='in', party_id=shop['customer_id'])
    r = shop['owner'].post(f'/api/sub/retail/cheques/{cheque_id}/bounce', json={'reason': 'insufficient funds'})
    assert r.status_code == 200, r.get_json()
    assert _cheque_status(shop['owner'], cheque_id) == 'bounced'

    r = shop['owner'].post(f'/api/sub/retail/cheques/{cheque_id}/reinstate', json={})
    assert r.status_code == 200, r.get_json()
    assert _cheque_status(shop['owner'], cheque_id) == 'pending'


def test_owner_gets_200_on_cheque_write_off(shop):
    cheque_id = _create_cheque(shop['owner'], direction='in', party_id=shop['customer_id'])
    r = shop['owner'].post(f'/api/sub/retail/cheques/{cheque_id}/bounce', json={'reason': 'insufficient funds'})
    assert r.status_code == 200, r.get_json()

    r = shop['owner'].post(f'/api/sub/retail/cheques/{cheque_id}/write-off', json={})
    assert r.status_code == 200, r.get_json()
    assert _cheque_status(shop['owner'], cheque_id) == 'written_off'


def test_owner_gets_200_on_cheque_endorse(shop):
    cheque_id = _create_cheque(shop['owner'], direction='in', party_id=shop['customer_id'])
    r = shop['owner'].post(f'/api/sub/retail/cheques/{cheque_id}/endorse', json={
        'to_party_type': 'supplier', 'to_party_id': shop['supplier_id'],
    })
    assert r.status_code == 200, r.get_json()
    assert _cheque_status(shop['owner'], cheque_id) == 'endorsed'


def test_owner_gets_200_on_cheque_cancel(shop):
    cheque_id = _create_cheque(shop['owner'], direction='in', party_id=shop['customer_id'])
    r = shop['owner'].post(f'/api/sub/retail/cheques/{cheque_id}/cancel', json={})
    assert r.status_code == 200, r.get_json()
    assert _cheque_status(shop['owner'], cheque_id) == 'cancelled'


# ── supplier_payment: void_payment's deny/allow (reversing) tests are
#    above -- this is the CREATE half of the same money-out authority. ────

def test_delegated_manager_denied_owner_only_on_supplier_payment_create(shop):
    before = _active_supplier_payment_count(shop['company_id'])
    r = shop['manager'].post(f'/api/sub/retail/suppliers/{shop["supplier_id"]}/payments',
                              json={'amount': 9, 'method': 'cash'})
    assert r.status_code == 403, r.get_json()
    assert r.get_json().get('code') == 'OWNER_ONLY', r.get_json()
    assert _active_supplier_payment_count(shop['company_id']) == before, (
        "a denied manager's supplier payment posted anyway")


def test_owner_gets_200_on_supplier_payment_create(shop):
    before = _active_supplier_payment_count(shop['company_id'])
    r = shop['owner'].post(f'/api/sub/retail/suppliers/{shop["supplier_id"]}/payments',
                            json={'amount': 2, 'method': 'cash'})
    assert r.status_code == 200, r.get_json()
    assert _active_supplier_payment_count(shop['company_id']) == before + 1


# ── business_day_settings_set ────────────────────────────────────────────

def _business_day_start_hour(company_id):
    conn = get_retail_conn()
    row = conn.execute(
        "SELECT svalue FROM retail_settings WHERE company_id=? AND skey='business_day_start_hour'",
        (company_id,),
    ).fetchone()
    conn.close()
    return row['svalue'] if row else None


def test_delegated_manager_denied_owner_only_on_business_day_settings(shop):
    # `business_day_start_hour` (a plain 0-23 int), not `business_timezone`
    # (an IANA zone name): business_day_settings_set's own docstring warns
    # that zone validation 503s on any install with no `tzdata` package,
    # which this dev machine (Windows) is one of -- the hour field proves
    # the SAME owner-only gate with no OS timezone-database dependency.
    before = _business_day_start_hour(shop['company_id'])
    r = shop['manager'].post('/api/sub/retail/settings/business-day', json={'business_day_start_hour': 6})
    assert r.status_code == 403, r.get_json()
    assert r.get_json().get('code') == 'OWNER_ONLY', r.get_json()
    assert _business_day_start_hour(shop['company_id']) == before


def test_owner_gets_200_on_business_day_settings(shop):
    r = shop['owner'].post('/api/sub/retail/settings/business-day', json={'business_day_start_hour': 6})
    assert r.status_code == 200, r.get_json()
    assert _business_day_start_hour(shop['company_id']) == '6'


# ── payment_methods_add ──────────────────────────────────────────────────

def _payment_method_count(company_id):
    conn = get_retail_conn()
    n = conn.execute("SELECT COUNT(*) FROM payment_methods WHERE company_id=?", (company_id,)).fetchone()[0]
    conn.close()
    return n


def test_delegated_manager_denied_owner_only_on_payment_methods_add(shop):
    before = _payment_method_count(shop['company_id'])
    r = shop['manager'].post('/api/sub/retail/payment-methods', json={'name': 'Manager Tender'})
    assert r.status_code == 403, r.get_json()
    assert r.get_json().get('code') == 'OWNER_ONLY', r.get_json()
    assert _payment_method_count(shop['company_id']) == before


def test_owner_gets_200_on_payment_methods_add(shop):
    before = _payment_method_count(shop['company_id'])
    r = shop['owner'].post('/api/sub/retail/payment-methods', json={'name': f'Owner Tender {uuid.uuid4().hex[:6]}'})
    assert r.status_code == 200, r.get_json()
    assert _payment_method_count(shop['company_id']) == before + 1


# ── demo_wipe / demo_seed -- see `demo_shop` fixture's own docstring for
#    why these use an isolated company rather than the shared `shop`. ─────

def _confirm(prefix, company_id):
    return f'{prefix}-{company_id}'


def test_delegated_manager_denied_owner_only_on_demo_wipe_and_seed(demo_shop):
    r = demo_shop['manager'].delete('/api/sub/retail/demo-wipe', json={})
    assert r.status_code == 403, r.get_json()
    assert r.get_json().get('code') == 'OWNER_ONLY', r.get_json()

    r = demo_shop['manager'].post('/api/sub/retail/demo-seed', json={})
    assert r.status_code == 403, r.get_json()
    assert r.get_json().get('code') == 'OWNER_ONLY', r.get_json()


def test_owner_gets_200_on_demo_wipe_and_seed(demo_shop):
    owner = demo_shop['owner']
    cid = demo_shop['company_id']
    r = owner.post('/api/sub/retail/products', json={
        'name': 'Demo Wipe Target', 'sku': f'DW-{uuid.uuid4().hex[:8]}',
        'sell_price': 5.0, 'tax_rate': 0, 'initial_stock': 1,
    })
    assert r.status_code == 200, r.get_json()

    r = owner.delete('/api/sub/retail/demo-wipe', json={'confirm': _confirm('WIPE', cid)})
    assert r.status_code == 200, r.get_json()
    conn = get_retail_conn()
    n = conn.execute("SELECT COUNT(*) FROM products WHERE company_id=?", (cid,)).fetchone()[0]
    conn.close()
    assert n == 0, "demo_wipe as the owner did not actually wipe the company's products"

    r = owner.post('/api/sub/retail/demo-seed', json={'confirm': _confirm('SEED', cid)})
    assert r.status_code == 200, r.get_json()
    conn = get_retail_conn()
    n = conn.execute("SELECT COUNT(*) FROM products WHERE company_id=?", (cid,)).fetchone()[0]
    conn.close()
    assert n > 0, "demo_seed as the owner did not actually seed any products"


# ── inventory_reconciliation (report) / repair_inventory_reconciliation
#    (write) -- isolated company too, since the repair half REWRITES cached
#    stock balances company-wide and demo_shop is already the "own isolated
#    company, function-scoped" fixture this pair needs. ────────────────────

def test_delegated_manager_denied_owner_only_on_inventory_reconciliation(demo_shop):
    r = demo_shop['manager'].get('/api/sub/retail/inventory/reconciliation')
    assert r.status_code == 403, r.get_json()
    assert r.get_json().get('code') == 'OWNER_ONLY', r.get_json()

    r = demo_shop['manager'].post('/api/sub/retail/inventory/reconciliation/repair', json={})
    assert r.status_code == 403, r.get_json()
    assert r.get_json().get('code') == 'OWNER_ONLY', r.get_json()


def test_owner_gets_200_on_inventory_reconciliation(demo_shop):
    owner = demo_shop['owner']
    r = owner.get('/api/sub/retail/inventory/reconciliation')
    assert r.status_code == 200, r.get_json()
    data = r.get_json()['data']
    assert set(data.keys()) >= {'drift_count', 'net_drift', 'pairs_examined', 'repair_confirmation', 'rows'}, data

    r = owner.post('/api/sub/retail/inventory/reconciliation/repair',
                    json={'confirm': data['repair_confirmation']})
    assert r.status_code == 200, r.get_json()


# ═════════════════════════════════════════════════════════════════════════
# ── D7: two branches, a scoped manager, and this device's OWN branch pin ────
# P0 round 2 (item #2 (I-10) D7 pin-bypass sibling, adversarial-verifier
# finding): `set_device_branch` (POST /device/branch) is deliberately NOT
# owner-gated (it re-pins only THIS till, never a company-wide setting --
# see that route's own docstring), but it had NO branch-scope check at all,
# unlike every OTHER branch-dimensioned write in retail_api.py. A manager
# scoped to Branch A -- CAP_EMPLOYEES, exactly what the branch-staff
# delegation toggle this whole file is about grants -- could POST
# `{"branch_uid": "<Branch B>"}` and re-pin THIS TILL to Branch B: every
# write on it that then omits an explicit branch_id (the manager's own
# stock adjustments and sales, and every OTHER cashier's sales on the same
# till) silently filed under Branch B instead of A (probe4.log
# `d7_pin_bypass`). A bare `{}` (unpin) is refused identically -- it hands
# the NEXT write to `_default_branch`'s self-heal, exactly as unaccountable
# for a scoped caller as a foreign pin, and the same probe showed it can
# also silently deny a branch's till (a Branch-B cashier's next sale
# refused "Insufficient stock" against the wrong branch's empty stock).
#
# A second, PRE-EXISTING D7 gap lives in `adjust_stock`: an EXPLICIT
# `branch_id` was resolved with its own local company-scoped lookup and
# never went through `_resolve_working_branch`'s D7 refusal at all, so a
# scoped manager (any branch-scoped manager has CAP_STOCK_ADJUST regardless
# of the delegation toggle) could name a foreign, SAME-company branch_id
# outright and move its stock -- inventory value in the books -- with no
# refusal (probe4.log control).
#
# `_device_branch_pin` resets `config.json`'s `branch_uid` key before AND
# after every test below: the pin is DEVICE-local state (onboarding_
# routes.py's own docstring), shared by every test in this module's one
# Flask app / one AURA_APP_DATA, so a test that left a foreign pin set
# would silently poison every test defined after it in this file.
# ═════════════════════════════════════════════════════════════════════════

@pytest.fixture
def _device_branch_pin():
    from commercial_runtime.identity.onboarding_routes import set_device_branch_uid
    set_device_branch_uid(None)
    yield
    set_device_branch_uid(None)


def _second_branch(owner, company_id):
    """A SECOND, real branch of `company_id`, created through the same
    owner-only POST /branches every other branch in this file comes
    through (see `_branch_count`'s sibling tests above) -- never a
    fabricated id/uid, so the pin-bypass tests below target a branch as
    real as one a shop could actually have. Returns (branch_id, branch_uid).
    """
    r = owner.post('/api/sub/retail/branches', json={'name': f'D7 Branch B {uuid.uuid4().hex[:6]}'})
    assert r.status_code == 200, r.get_json()
    bid = r.get_json()['data']['id']
    conn = get_retail_conn()
    row = conn.execute("SELECT uid FROM branches WHERE id=? AND company_id=?", (bid, company_id)).fetchone()
    conn.close()
    return bid, row['uid']


def _own_branch_id(company_id, branch_uid):
    conn = get_retail_conn()
    row = conn.execute("SELECT id FROM branches WHERE uid=? AND company_id=?", (branch_uid, company_id)).fetchone()
    conn.close()
    return row['id']


def test_delegated_manager_cannot_pin_till_to_foreign_branch(shop, _device_branch_pin):
    _, branch_b_uid = _second_branch(shop['owner'], shop['company_id'])
    r = shop['manager'].post('/api/sub/retail/device/branch', json={'branch_uid': branch_b_uid})
    assert r.status_code == 403, r.get_json()

    # The pin itself must be UNCHANGED, not merely refused with a stale
    # response body -- a 403 that still wrote the pin would be worse than
    # no check at all.
    check = shop['manager'].get('/api/sub/retail/device/branch')
    assert check.status_code == 200, check.get_json()
    assert check.get_json()['data']['branch_uid'] != branch_b_uid, "the refused pin landed anyway"


def test_delegated_manager_cannot_unpin_till(shop, _device_branch_pin):
    pin = shop['manager'].post('/api/sub/retail/device/branch', json={'branch_uid': shop['branch_uid']})
    assert pin.status_code == 200, pin.get_json()  # pinning to OWN branch is the allow half, below

    r = shop['manager'].post('/api/sub/retail/device/branch', json={})
    assert r.status_code == 403, r.get_json()

    check = shop['manager'].get('/api/sub/retail/device/branch')
    assert check.status_code == 200, check.get_json()
    assert check.get_json()['data']['branch_uid'] == shop['branch_uid'], "the refused unpin cleared the pin anyway"


def test_delegated_manager_gets_200_pinning_own_branch(shop, _device_branch_pin):
    """The allow half: the D7 refusal must not become a blanket ban on this
    route for a scoped caller -- pinning the till to their OWN branch is
    exactly what the toggle promises this manager can do at their terminal.
    """
    r = shop['manager'].post('/api/sub/retail/device/branch', json={'branch_uid': shop['branch_uid']})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['data']['branch_uid'] == shop['branch_uid']


def test_owner_gets_200_pinning_till_anywhere(shop, _device_branch_pin):
    """The owner is never scoped (`session_branch_scope()` returns `None`
    for role admin, structurally) -- D7 must not touch this route for them
    at all, pin or unpin."""
    _, branch_b_uid = _second_branch(shop['owner'], shop['company_id'])
    r = shop['owner'].post('/api/sub/retail/device/branch', json={'branch_uid': branch_b_uid})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['data']['branch_uid'] == branch_b_uid

    r = shop['owner'].post('/api/sub/retail/device/branch', json={})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['data']['branch_uid'] is None


def test_delegated_manager_cannot_stock_adjust_a_foreign_branch(shop, _device_branch_pin):
    """Pre-existing D7 gap, not new to round 2: adjust_stock's explicit
    `branch_id` tier never called `_resolve_working_branch`, so it skipped
    that helper's OWN-branch refusal entirely (its cross-tenant "does this
    branch belong to my company" check still ran, and still does -- see
    this route's own comment for why the cross-tenant 404 stays exactly as
    it was)."""
    branch_b_id, _ = _second_branch(shop['owner'], shop['company_id'])
    r = shop['manager'].post(
        f'/api/sub/retail/products/{shop["product_id"]}/stock-adjust',
        json={'quantity': -5, 'branch_id': branch_b_id})
    assert r.status_code == 403, r.get_json()

    conn = get_retail_conn()
    row = conn.execute(
        "SELECT quantity_on_hand FROM inventory_balances WHERE company_id=? AND product_id=? AND branch_id=?",
        (shop['company_id'], shop['product_id'], branch_b_id)).fetchone()
    conn.close()
    assert row is None or float(row['quantity_on_hand'] or 0) == 0.0, "a denied stock-adjust landed anyway"


def test_delegated_manager_gets_200_stock_adjusting_own_branch(shop, _device_branch_pin):
    """The allow half: a positive quantity, so the assertion cannot be
    confused with the (unrelated) "not enough on hand" 400 -- this test's
    only job is proving the D7 check does not become a blanket ban."""
    own_bid = _own_branch_id(shop['company_id'], shop['branch_uid'])
    before = get_retail_conn()
    before_row = before.execute(
        "SELECT COALESCE(quantity_on_hand,0) AS q FROM inventory_balances WHERE company_id=? AND product_id=? AND branch_id=?",
        (shop['company_id'], shop['product_id'], own_bid)).fetchone()
    before.close()
    before_qty = float(before_row['q']) if before_row else 0.0

    r = shop['manager'].post(
        f'/api/sub/retail/products/{shop["product_id"]}/stock-adjust',
        json={'quantity': 3, 'branch_id': own_bid})
    assert r.status_code == 200, r.get_json()

    conn = get_retail_conn()
    row = conn.execute(
        "SELECT quantity_on_hand FROM inventory_balances WHERE company_id=? AND product_id=? AND branch_id=?",
        (shop['company_id'], shop['product_id'], own_bid)).fetchone()
    conn.close()
    assert float(row['quantity_on_hand']) == before_qty + 3


def test_owner_gets_200_stock_adjusting_any_branch(shop, _device_branch_pin):
    branch_b_id, _ = _second_branch(shop['owner'], shop['company_id'])
    r = shop['owner'].post(
        f'/api/sub/retail/products/{shop["product_id"]}/stock-adjust',
        json={'quantity': 2, 'branch_id': branch_b_id})
    assert r.status_code == 200, r.get_json()


def test_scoped_manager_pinned_foreign_device_refused_on_implicit_branch_write(shop, _device_branch_pin):
    """Defense in depth (`_resolve_working_branch`'s own D7 comment, round
    2): a foreign pin can reach a scoped caller through a path OTHER than
    `set_device_branch` -- a device pinned before this round's fix landed
    and never re-pinned, or an owner deliberately sharing one till across
    branches. Writing `config.json`'s `branch_uid` key directly (bypassing
    the route-level check entirely) simulates exactly that, and proves the
    PINNED tier's own refusal, not just the route that normally sets it."""
    from commercial_runtime.identity.onboarding_routes import set_device_branch_uid
    _, branch_b_uid = _second_branch(shop['owner'], shop['company_id'])
    set_device_branch_uid(branch_b_uid)

    r = shop['manager'].post(
        f'/api/sub/retail/products/{shop["product_id"]}/stock-adjust',
        json={'quantity': 2})  # no explicit branch_id -- relies on the device's pin
    assert r.status_code == 403, r.get_json()


def test_scoped_manager_pinned_own_branch_gets_200_on_implicit_branch_write(shop, _device_branch_pin):
    """The allow half: a device genuinely pinned to the caller's OWN branch
    (the normal case for a real till) must not be refused by the same
    defense-in-depth check."""
    from commercial_runtime.identity.onboarding_routes import set_device_branch_uid
    set_device_branch_uid(shop['branch_uid'])

    r = shop['manager'].post(
        f'/api/sub/retail/products/{shop["product_id"]}/stock-adjust',
        json={'quantity': 1})
    assert r.status_code == 200, r.get_json()


# ═════════════════════════════════════════════════════════════════════════
# ── P0 round 3 (item V1): the dead `role_level >= 4` bypass clause is gone,
#    and the 403 body now carries a client-facing `message` ─────────────────
# Round-2 reviewer finding: `_require_company_admin` read
# `session.get('mt_role') != 'admin' and session.get('role_level', 0) < 4` --
# a no-op today (grep across every *.py in this repo for an assignment to
# `session['role_level']` finds none), but a LATENT bypass the day anything
# ever sets that key, since the `and`'s second operand is always True while
# nothing sets it. And the 403 body carried only `error`, never `message` --
# the client's generic `_post` error toast reads `resp.message`
# (subsystem-retail.js), so a denied manager saw no toast at all. Every
# EXISTING test in this file above already asserts `code == 'OWNER_ONLY'`
# without reading `message`, so tightening the predicate and adding the key
# cannot regress any of them -- this section only ADDS the message
# assertion, it does not touch any assertion already in the file.
# ═════════════════════════════════════════════════════════════════════════

def test_owner_only_403_carries_a_client_facing_message(shop):
    """The client's toast path reads `resp.message`, not `resp.error` -- a
    body with only `error` (the pre-fix shape) left a denied manager with a
    silent 403. Checked on a real route from this same file's own deny list
    (`/settings/tax`) rather than calling `_require_company_admin` directly,
    matching this file's own 'deliberately real HTTP throughout' rule."""
    r = shop['manager'].post('/api/sub/retail/settings/tax', json={'tax_calculation_mode': 'after_discount'})
    assert r.status_code == 403, r.get_json()
    body = r.get_json()
    assert body.get('code') == 'OWNER_ONLY', body
    assert body.get('message'), f"403 body has no client-facing message: {body!r}"
    assert body['message'] == body.get('error'), (
        "message and error must carry the identical sentence -- the client "
        f"may read either key depending on call site: {body!r}"
    )
    assert body['message'] == 'Only the shop owner can change this.', body


def test_owner_only_403_still_fires_with_a_role_level_key_present_in_session(shop):
    """MUTATION-SHAPED allow-half proof for the dead bypass clause itself:
    this is the exact scenario the old `and session.get('role_level', 0) < 4`
    clause would have mishandled the day anything ever set that key. Nothing
    in this codebase sets `session['role_level']` today (confirmed by grep,
    see `_require_company_admin`'s own docstring), so this test manufactures
    the one state the dead clause could ever have acted on, to prove the
    fixed predicate no longer even looks at it. Before the round-3 fix: a
    delegated manager with `role_level=4` injected into their session would
    have PASSED this owner-only gate (`'admin' != 'admin'` is True, but
    `4 < 4` is False, so the `and` is False -- guard returns None, admits).
    After the fix: the guard reads only `mt_role`, so this manager is still
    refused."""
    with shop['manager'].session_transaction() as sess:
        sess['role_level'] = 4
    r = shop['manager'].post('/api/sub/retail/settings/tax', json={'tax_calculation_mode': 'after_discount'})
    assert r.status_code == 403, (
        "a session carrying role_level=4 must not bypass the owner-only "
        f"check through the removed dead clause: {r.get_json()!r}"
    )
    assert r.get_json().get('code') == 'OWNER_ONLY', r.get_json()
