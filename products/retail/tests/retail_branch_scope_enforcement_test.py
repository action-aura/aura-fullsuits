"""
Aura Retail -- branch-scope data-plane enforcement (launch-readiness
account-hierarchy design §3.3/§4.2 D7, wave C2/Reading A).

Covers the two halves D7 adds on top of C1's device->branch pinning
(retail_device_branch_pin_test.py) and the pre-existing report `?branch_id=`
filter (retail_report_branch_filter_test.py):

  READS  -- a scoped caller's `?branch_id=` is COERCED server-side to their
            own branch, regardless of what (if anything) the query string
            named (`_coerce_branch_id_for_scope`, retail_api.py).
  WRITES -- a scoped caller naming a FOREIGN `branch_id` explicitly is
            REFUSED (`_resolve_working_branch`, retail_api.py).

Plus the contract that makes both of the above safe to ship: NULL scope
(every existing account today, and every account this wave does not touch)
behaves EXACTLY as it did before this wave existed -- the invisible-unless-
configured guarantee this whole design leans on -- and an admin is NEVER
scoped, so nothing above ever touches the owner's own reads or writes.

Self-contained bootstrap, matching retail_device_branch_pin_test.py /
retail_report_branch_filter_test.py (no shared conftest.py exists here).

Run:
    pytest products/retail/tests/retail_branch_scope_enforcement_test.py -v
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
TESTS_DIR = Path(__file__).resolve().parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR), str(TESTS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_branchscope_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402

seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")
os.environ.pop("AURA_RETAIL_DEMO_MODE", None)

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

import api.retail_api as retail_api  # noqa: E402 -- the SAME module object app.py registered
from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402
from commercial_runtime.identity import onboarding_routes as _onb  # noqa: E402
from database.schema import get_retail_conn  # noqa: E402


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


# ── Fixtures / helpers (deliberately duplicated per-file convention -- see
#    the sibling module docstrings this file mirrors) ───────────────────────

def _new_company(role="admin"):
    email = f"bscope-{uuid.uuid4().hex[:10]}@test.local"
    password = "BranchScopePW1"
    company_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, require_password_change) "
        "VALUES (?,?,?,?,?,?,?,0)",
        (user_id, company_id, "EMP-0001", email, hash_password(password), role, "active"),
    )
    conn.commit()
    conn.close()
    return company_id, user_id, email, password


def _login(email, password):
    c = app.test_client()
    r = c.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.get_json()
    c.get("/api/sub/retail/settings/tax")  # forces the lazy schema helpers, same as sibling files
    return c


@pytest.fixture(autouse=True)
def _reset_device_pin():
    """No test in this file relies on a device pin -- every mutation names
    `branch_id` explicitly, which is what D7's write-side guard actually
    gates. Reset anyway, matching retail_device_branch_pin_test.py's own
    autouse fixture, so a pin a DIFFERENT test file left behind (config.json
    is shared per-process) can never leak in here."""
    _onb.set_device_branch_uid(None)
    yield
    _onb.set_device_branch_uid(None)


def _create_branch(client, name):
    r = client.post('/api/sub/retail/branches', json={'name': name})
    assert r.status_code == 200, r.get_json()
    bid = r.get_json()['data']['id']
    conn = get_retail_conn()
    row = conn.execute("SELECT uid FROM branches WHERE id=?", (bid,)).fetchone()
    conn.close()
    return bid, row['uid']


def _create_product(client, initial_stock=0, price=50.0):
    # price=50.0 to match _sell()'s own default `price` param (its
    # `amount_paid` computation) -- zero tax keeps expected totals trivial
    # to hand-compute (total == quantity * price), same convention
    # retail_report_branch_filter_test.py's own fixture uses.
    sku = f"BSCP-{uuid.uuid4().hex[:8]}"
    r = client.post("/api/sub/retail/products", json={
        "name": f"Branch scope test item {sku}", "sku": sku, "sell_price": price,
        "cost_price": price / 2, "tax_rate": 0, "initial_stock": initial_stock,
    })
    assert r.status_code == 200, r.get_json()
    return r.get_json()["data"]["id"], sku


def _seed_stock(client, pid, branch_id, qty):
    r = client.post(f"/api/sub/retail/products/{pid}/stock-adjust",
                     json={"quantity": qty, "reason": "seed", "branch_id": branch_id})
    assert r.status_code == 200, r.get_json()


def _sell(client, pid, branch_id, qty, price=50.0):
    r = client.post('/api/sub/retail/sales', json={
        'items': [{'product_id': pid, 'quantity': qty}],
        'branch_id': branch_id,
        'payment_method': 'cash',
        'amount_paid': qty * price,
        'idempotency_key': str(uuid.uuid4()),
    })
    return r


def _set_scope(user_id, branch_uid):
    """Sets `users.branch_scope_uid` directly -- D5's own route (PUT
    /api/admin/employees/<id>/branch-scope) is proven correct on its own
    terms in commercial_runtime/identity/tests/test_employee_admin_routes.py;
    this file is about D7's DATA-PLANE consumption of whatever scope is
    stored, so setting it directly (matching retail_device_branch_pin_test.
    py's own `_onb.set_device_branch_uid` direct-state-injection convention)
    keeps each suite testing its own layer."""
    conn = registry_conn()
    conn.execute("UPDATE users SET branch_scope_uid=? WHERE id=?", (branch_uid, user_id))
    conn.commit()
    conn.close()


def _invite_manager(admin_client):
    email = f"bscope-mgr-{uuid.uuid4().hex[:8]}@test.local"
    # `permissions: {'retail': 'full'}` grants the LEGACY subsystem gate
    # `mt_require_subsystem('retail')` reads on every route below -- the
    # eight namespaced capability codes (seeded from `role='manager'`) are a
    # SEPARATE, finer gate stacked on top, and grant nothing on their own.
    # Matches retail_employee_management_test.py's own invite shape for the
    # identical reason.
    r = admin_client.post('/api/admin/employees', json={
        'email': email, 'role': 'manager', 'permissions': {'retail': 'full'},
    })
    assert r.status_code == 200, r.get_json()
    conn = registry_conn()
    row = conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
    user_id = row['id']
    password = "ManagerScopePW1"
    conn.execute("UPDATE users SET password_hash=?, status='active' WHERE id=?",
                 (hash_password(password), user_id))
    conn.commit()
    conn.close()
    return user_id, email, password


@pytest.fixture
def two_branch_company():
    """One company, owner + a manager, two real branches (Main/Downtown),
    one product stocked and priced identically in both -- a deterministic
    split so coercion/refusal assertions are testing the SCOPE LOGIC, not
    arithmetic."""
    company_id, admin_id, admin_email, admin_password = _new_company(role="admin")
    admin = _login(admin_email, admin_password)
    main_id, main_uid = _create_branch(admin, 'Main')
    down_id, down_uid = _create_branch(admin, 'Downtown')

    pid, _ = _create_product(admin, initial_stock=0)
    _seed_stock(admin, pid, main_id, 1000)
    _seed_stock(admin, pid, down_id, 1000)

    manager_id, manager_email, manager_password = _invite_manager(admin)

    return {
        'company_id': company_id, 'admin': admin, 'admin_id': admin_id,
        'main_id': main_id, 'main_uid': main_uid,
        'down_id': down_id, 'down_uid': down_uid,
        'pid': pid,
        'manager_id': manager_id, 'manager_email': manager_email, 'manager_password': manager_password,
    }


# ═════════════════════════════════════════════════════════════════════════════
# (3) NULL scope = every branch -- an UNSCOPED non-admin behaves EXACTLY as
#     before this wave. This is the invisible-unless-configured contract and
#     covers nearly every install today.
# ═════════════════════════════════════════════════════════════════════════════

def test_unscoped_manager_reads_any_branch_unfiltered(two_branch_company):
    fx = two_branch_company
    _sell(fx['admin'], fx['pid'], fx['main_id'], 2)
    _sell(fx['admin'], fx['pid'], fx['down_id'], 3)

    manager = _login(fx['manager_email'], fx['manager_password'])
    # No scope set at all (the default for every existing account) -- an
    # explicit ?branch_id= for EITHER branch, or no filter at all, must
    # behave exactly as it did before D7 was ever written.
    down_only = manager.get(f"/api/sub/retail/reports/summary?days=365&branch_id={fx['down_id']}").get_json()
    assert down_only['data']['revenue'] == 150.0  # 3 * 50

    unfiltered = manager.get('/api/sub/retail/reports/summary?days=365').get_json()
    assert unfiltered['data']['revenue'] == 250.0  # (2+3) * 50


def test_unscoped_manager_can_write_to_any_branch_explicitly(two_branch_company):
    fx = two_branch_company
    manager = _login(fx['manager_email'], fx['manager_password'])

    r = _sell(manager, fx['pid'], fx['down_id'], 1)
    assert r.status_code == 200, r.get_json()

    conn = get_retail_conn()
    row = conn.execute("SELECT branch_id FROM sales WHERE id=?", (r.get_json()['data']['id'],)).fetchone()
    conn.close()
    assert row['branch_id'] == fx['down_id']


# ═════════════════════════════════════════════════════════════════════════════
# (4) A scoped user's report read is COERCED to their branch when they ask
#     for another's -- never an error, never a probe surface.
# ═════════════════════════════════════════════════════════════════════════════

def test_scoped_manager_read_is_coerced_to_their_own_branch(two_branch_company):
    fx = two_branch_company
    _sell(fx['admin'], fx['pid'], fx['main_id'], 4)     # $200
    _sell(fx['admin'], fx['pid'], fx['down_id'], 6)     # $300

    _set_scope(fx['manager_id'], fx['main_uid'])
    manager = _login(fx['manager_email'], fx['manager_password'])

    # Explicitly asks for Downtown -- must get MAIN's number back, not
    # Downtown's, and not a refusal.
    r = manager.get(f"/api/sub/retail/reports/summary?days=365&branch_id={fx['down_id']}")
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['data']['revenue'] == 200.0, \
        "a scoped caller's foreign branch_id must be COERCED to their own branch, not honoured"

    # Omitting branch_id entirely must ALSO coerce to Main -- not the
    # unscoped/all-branches total.
    r2 = manager.get('/api/sub/retail/reports/summary?days=365')
    assert r2.get_json()['data']['revenue'] == 200.0, \
        "a scoped caller with NO branch_id must still be confined to their own branch"


def test_scoped_manager_cannot_probe_which_branches_exist_via_error_shape(two_branch_company):
    """Design §3.3: 'they must not be able to probe' -- a scoped caller's
    request for a real OTHER branch and a request for a NONEXISTENT branch
    id must be indistinguishable (both silently coerced to the caller's own
    branch), never a 400/404 that would let a request leak which ids are
    real."""
    fx = two_branch_company
    _set_scope(fx['manager_id'], fx['main_uid'])
    manager = _login(fx['manager_email'], fx['manager_password'])

    real_other = manager.get(f"/api/sub/retail/reports/summary?days=365&branch_id={fx['down_id']}")
    nonexistent = manager.get("/api/sub/retail/reports/summary?days=365&branch_id=999999")
    assert real_other.status_code == 200
    assert nonexistent.status_code == 200
    assert real_other.get_json() == nonexistent.get_json()


def test_scoped_manager_read_is_coerced_across_every_covered_report_route(two_branch_company):
    """One deep test above (summary) plus a shallow sweep over the rest of
    D7's route list, so a route added to `_coerce_branch_id_for_scope`'s
    call sites but wired wrong is not the ONE route this suite happens to
    check deeply."""
    fx = two_branch_company
    _sell(fx['admin'], fx['pid'], fx['main_id'], 1)   # $50
    _sell(fx['admin'], fx['pid'], fx['down_id'], 5)   # $250
    _set_scope(fx['manager_id'], fx['main_uid'])
    manager = _login(fx['manager_email'], fx['manager_password'])

    dash = manager.get(f"/api/sub/retail/dashboard/stats?branch_id={fx['down_id']}").get_json()
    assert dash['data']['today_sales'] in (0.0, 50.0)  # today's window, not the deciding factor here
    trend = manager.get(f"/api/sub/retail/reports/sales-trend?days=365&branch_id={fx['down_id']}").get_json()
    assert sum(trend['data']) == 50.0
    top = manager.get(f"/api/sub/retail/reports/top-products?days=365&branch_id={fx['down_id']}").get_json()
    assert top['revenue'] == [50.0]
    pay = manager.get(f"/api/sub/retail/reports/payment-methods?days=365&branch_id={fx['down_id']}").get_json()
    assert sum(r['revenue'] for r in pay['data']) == 50.0


# ═════════════════════════════════════════════════════════════════════════════
# (5) A scoped user's mutation naming a FOREIGN branch is REFUSED.
# ═════════════════════════════════════════════════════════════════════════════

def test_scoped_manager_mutation_naming_a_foreign_branch_is_refused(two_branch_company):
    fx = two_branch_company
    _set_scope(fx['manager_id'], fx['main_uid'])
    manager = _login(fx['manager_email'], fx['manager_password'])

    r = _sell(manager, fx['pid'], fx['down_id'], 1)
    assert r.status_code == 403, r.get_json()
    body = r.get_json()
    assert body['status'] == 'error'

    conn = get_retail_conn()
    n = conn.execute("SELECT COUNT(*) FROM sales WHERE branch_id=?", (fx['down_id'],)).fetchone()[0]
    conn.close()
    assert n == 0, "a refused foreign-branch mutation must not leave a partial sale row"


def test_scoped_manager_can_still_write_to_their_own_branch(two_branch_company):
    """The allow-half -- a 'deny everything' mutation would pass the test
    above too; this is what tells the two apart."""
    fx = two_branch_company
    _set_scope(fx['manager_id'], fx['main_uid'])
    manager = _login(fx['manager_email'], fx['manager_password'])

    r = _sell(manager, fx['pid'], fx['main_id'], 1)
    assert r.status_code == 200, r.get_json()

    conn = get_retail_conn()
    row = conn.execute("SELECT branch_id FROM sales WHERE id=?", (r.get_json()['data']['id'],)).fetchone()
    conn.close()
    assert row['branch_id'] == fx['main_id']


def test_scoped_manager_with_no_explicit_branch_id_still_resolves_normally(two_branch_company):
    """D7's write-side guard is scoped, deliberately, to an EXPLICIT foreign
    branch_id (design §4.2's own instruction: '_resolve_working_branch is
    where write-side branch resolution already converges... make a scoped
    caller's FOREIGN EXPLICIT branch_id refuse there'). A scoped caller who
    sends no branch_id at all must still resolve via the ordinary pinned/
    default tiers, unaffected -- this pins that boundary so it is not
    mistaken for a gap later."""
    fx = two_branch_company
    _set_scope(fx['manager_id'], fx['main_uid'])
    manager = _login(fx['manager_email'], fx['manager_password'])

    r = manager.post('/api/sub/retail/sales', json={
        'items': [{'product_id': fx['pid'], 'quantity': 1}],
        'payment_method': 'cash', 'amount_paid': 50.0,
        'idempotency_key': str(uuid.uuid4()),
    })
    assert r.status_code == 200, r.get_json()


def test_scoped_manager_mutation_refusal_pins_the_response_status_and_message_shape(two_branch_company):
    fx = two_branch_company
    _set_scope(fx['manager_id'], fx['main_uid'])
    manager = _login(fx['manager_email'], fx['manager_password'])

    r = _sell(manager, fx['pid'], fx['down_id'], 1)
    assert r.status_code == 403
    assert r.get_json()['message'] == 'You may only write to your own branch.'


# ═════════════════════════════════════════════════════════════════════════════
# (6) An ADMIN is never scoped: full cross-branch read AND write, unchanged.
# ═════════════════════════════════════════════════════════════════════════════

def test_admin_is_never_scoped_full_cross_branch_read_and_write(two_branch_company):
    fx = two_branch_company
    admin = fx['admin']

    r = _sell(admin, fx['pid'], fx['down_id'], 2)
    assert r.status_code == 200, r.get_json()

    body = admin.get(f"/api/sub/retail/reports/summary?days=365&branch_id={fx['main_id']}").get_json()
    assert body['success'] is True  # not refused, not silently coerced away from what was asked

    unfiltered = admin.get('/api/sub/retail/reports/summary?days=365').get_json()
    assert unfiltered['data']['revenue'] == 100.0  # sees the Downtown sale too -- never scoped


# ═════════════════════════════════════════════════════════════════════════════
# (10) ASSERT THE CHECK RAN, not just the outcome -- a 403 alone cannot tell
#      "the scope guard refused" from "something else refused". Counts the
#      real call to session_branch_scope() that _resolve_working_branch
#      makes on the refusal path.
# ═════════════════════════════════════════════════════════════════════════════

def test_the_scope_check_actually_ran_for_the_mutation_refusal(two_branch_company, monkeypatch):
    fx = two_branch_company
    _set_scope(fx['manager_id'], fx['main_uid'])
    manager = _login(fx['manager_email'], fx['manager_password'])

    real_session_branch_scope = retail_api.session_branch_scope
    calls = {'n': 0}

    def _counting_scope():
        calls['n'] += 1
        return real_session_branch_scope()

    monkeypatch.setattr(retail_api, 'session_branch_scope', _counting_scope)

    r = _sell(manager, fx['pid'], fx['down_id'], 1)
    assert r.status_code == 403
    assert calls['n'] >= 1, \
        "_resolve_working_branch never called session_branch_scope() -- the 403 above " \
        "cannot be trusted to have come from the scope guard at all"
