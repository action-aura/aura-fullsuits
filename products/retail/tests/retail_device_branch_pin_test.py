"""
Aura Retail -- device-branch pinning: launch-readiness chain wave C1
(ROADMAP.md's 2026-08-30 "the multi-branch capture defect" entry;
docs/launch-readiness/seats-and-chain-design.md §5.2 gap 1).

THE DEFECT THIS FILE PROVES CLOSED. `_default_branch(conn, cid)`
(api/retail_api.py) resolves a company's working branch as the company's
FIRST branch by id, and the POS client sends no `branch_id` at all on a
sale. Nothing pinned a device to the branch it physically stands in, so on
a chain (one licence, every store's branch rows converged onto every till
by sync) every till filed every sale under store 1 and decremented store
1's stock.

THE FIX. A device-local `branch_uid` in config.json
(commercial_runtime/identity/onboarding_routes.py's get_device_branch_uid/
set_device_branch_uid -- deliberately a UID, never `branches.id`, which is a
plain per-device autoincrement; see api/retail_api.py's
`_resolve_working_branch` for the full three-tier resolution order this
file exercises: explicit request branch_id (validated) -> this device's
pinned branch -> `_default_branch` self-heal).

Self-contained bootstrap, matching retail_oversell_exception_test.py and
retail_route_capability_matrix_test.py (no shared conftest.py exists here).

Run:
    pytest products/retail/tests/retail_device_branch_pin_test.py -v
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_branchpin_"))
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
from commercial_runtime.identity import onboarding_routes as _onb  # noqa: E402
from database.schema import get_retail_conn  # noqa: E402


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


# ── Fixtures / helpers (deliberately duplicated per-file convention -- see
#    the module docstrings this file cites for why nothing here is shared
#    via a conftest.py) ───────────────────────────────────────────────────

def _new_company(role="admin"):
    email = f"branchpin-{uuid.uuid4().hex[:10]}@test.local"
    password = "BranchPinPW1"
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
    return company_id, email, password


def _login(email, password):
    c = app.test_client()
    r = c.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.get_json()
    c.get("/api/sub/retail/settings/tax")  # forces the lazy schema helpers, same as its sibling files
    return c


@pytest.fixture
def company():
    """One company per test -- branch/stock/sale rows are company-scoped,
    and a shared company would make one test's branch layout visible to
    another's assertions."""
    company_id, email, password = _new_company()
    return company_id, _login(email, password)


@pytest.fixture(autouse=True)
def _reset_device_pin():
    """The pin lives in config.json -- DEVICE-local, never company-scoped
    -- so it is NOT reset by the `company` fixture's fresh company/login.
    Every test in this module shares the one `AURA_APP_DATA` directory (and
    therefore the one config.json) for the whole process, exactly like a
    real till only ever has one config.json. Without this, a pin set by one
    test would leak into the next test's assertions regardless of which
    company is logged in -- clear it before AND after every test so
    ordering can never matter."""
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


def _create_product(client, initial_stock=0):
    sku = f"BPIN-{uuid.uuid4().hex[:8]}"
    r = client.post("/api/sub/retail/products", json={
        "name": f"Branch pin test item {sku}", "sku": sku, "sell_price": 10.0,
        "cost_price": 5.0, "tax_rate": 0, "initial_stock": initial_stock,
    })
    assert r.status_code == 200, r.get_json()
    return r.get_json()["data"]["id"], sku


def _seed_stock(client, pid, branch_id, qty):
    r = client.post(f"/api/sub/retail/products/{pid}/stock-adjust",
                     json={"quantity": qty, "reason": "seed", "branch_id": branch_id})
    assert r.status_code == 200, r.get_json()


def _sale(client, product_id, quantity=1, branch_id=None):
    payload = {
        'items': [{'product_id': product_id, 'quantity': quantity}],
        'amount_paid': 100000, 'payment_method': 'cash',
        'idempotency_key': str(uuid.uuid4()),
    }
    if branch_id is not None:
        payload['branch_id'] = branch_id
    return client.post('/api/sub/retail/sales', json=payload)


def _pin(client, uid):
    return client.post('/api/sub/retail/device/branch', json={'branch_uid': uid})


def _get_pin(client):
    r = client.get('/api/sub/retail/device/branch')
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']


def _balance(company_id, product_id, branch_id):
    conn = get_retail_conn()
    row = conn.execute(
        "SELECT quantity_on_hand FROM inventory_balances WHERE company_id=? AND product_id=? AND branch_id=?",
        (company_id, product_id, branch_id),
    ).fetchone()
    conn.close()
    return row['quantity_on_hand'] if row else None


def _sale_branch(sale_id):
    conn = get_retail_conn()
    row = conn.execute("SELECT branch_id FROM sales WHERE id=?", (sale_id,)).fetchone()
    conn.close()
    return row['branch_id']


def _audit_count(company_id, action):
    conn = get_retail_conn()
    n = conn.execute(
        "SELECT COUNT(*) FROM audit_log WHERE company_id=? AND action=?", (company_id, action)
    ).fetchone()[0]
    conn.close()
    return n


# ═════════════════════════════════════════════════════════════════════════════
# 1. THE DEFECT: a pin to the SECOND branch files the sale -- and decrements
#    the stock -- under that branch, not the first.
# ═════════════════════════════════════════════════════════════════════════════

def test_pinned_branch_files_sale_and_decrements_its_own_stock(company):
    company_id, client = company
    b1_id, b1_uid = _create_branch(client, 'Store 1')
    b2_id, b2_uid = _create_branch(client, 'Store 2')
    assert b1_id < b2_id  # _default_branch's own ORDER BY id LIMIT 1 would pick b1

    pid, _ = _create_product(client, initial_stock=0)
    _seed_stock(client, pid, b1_id, 10)
    _seed_stock(client, pid, b2_id, 10)

    r = _pin(client, b2_uid)
    assert r.status_code == 200, r.get_json()
    assert _get_pin(client)['branch_id'] == b2_id

    sale = _sale(client, pid, 3)
    assert sale.status_code == 200, sale.get_json()
    sale_id = sale.get_json()['data']['id']

    assert _sale_branch(sale_id) == b2_id, "the sale must file under the PINNED branch, not the first one"
    assert _balance(company_id, pid, b2_id) == 7.0, "stock must decrement at the pinned branch"
    assert _balance(company_id, pid, b1_id) == 10.0, "the unrelated first branch must be untouched"


# ═════════════════════════════════════════════════════════════════════════════
# 2. UNCHANGED WHEN UNPINNED: no pin set -> behaviour is exactly as before
#    (_default_branch, the company's first branch).
# ═════════════════════════════════════════════════════════════════════════════

def test_unpinned_device_behaves_exactly_as_before(company):
    company_id, client = company
    b1_id, _ = _create_branch(client, 'Store 1')
    b2_id, _ = _create_branch(client, 'Store 2')

    pid, _ = _create_product(client, initial_stock=0)
    _seed_stock(client, pid, b1_id, 5)
    _seed_stock(client, pid, b2_id, 5)

    assert _get_pin(client)['branch_uid'] is None, "no pin was ever set for this test"

    sale = _sale(client, pid, 2)
    assert sale.status_code == 200, sale.get_json()
    sale_id = sale.get_json()['data']['id']

    assert _sale_branch(sale_id) == b1_id, "unpinned must still resolve to _default_branch's own answer"
    assert _balance(company_id, pid, b1_id) == 3.0
    assert _balance(company_id, pid, b2_id) == 5.0


# ═════════════════════════════════════════════════════════════════════════════
# 3. An explicit request branch_id still wins over the pin.
# ═════════════════════════════════════════════════════════════════════════════

def test_explicit_branch_id_wins_over_the_pin(company):
    company_id, client = company
    b1_id, _ = _create_branch(client, 'Store 1')
    b2_id, b2_uid = _create_branch(client, 'Store 2')

    pid, _ = _create_product(client, initial_stock=0)
    _seed_stock(client, pid, b1_id, 5)
    _seed_stock(client, pid, b2_id, 5)

    assert _pin(client, b2_uid).status_code == 200

    # Pinned to b2, but this request explicitly names b1.
    sale = _sale(client, pid, 1, branch_id=b1_id)
    assert sale.status_code == 200, sale.get_json()
    sale_id = sale.get_json()['data']['id']

    assert _sale_branch(sale_id) == b1_id, "an explicit branch_id must outrank this device's own pin"
    assert _balance(company_id, pid, b1_id) == 4.0
    assert _balance(company_id, pid, b2_id) == 5.0


# ═════════════════════════════════════════════════════════════════════════════
# 4. An explicit request branch_id belonging to ANOTHER company is refused.
# ═════════════════════════════════════════════════════════════════════════════

def test_explicit_branch_id_from_another_company_is_refused(company):
    company_id, client = company
    _create_branch(client, 'Store 1')
    pid, _ = _create_product(client, initial_stock=5)

    other_company_id, other_email, other_password = _new_company()
    other_client = _login(other_email, other_password)
    foreign_bid, _ = _create_branch(other_client, 'Someone Else\'s Store')

    r = _sale(client, pid, 1, branch_id=foreign_bid)
    assert r.status_code == 400, r.get_json()
    body = r.get_json()
    assert body['status'] == 'error'
    assert str(foreign_bid) in body['message']

    # And nothing was written: the same idempotency-free re-check via a
    # fresh sale must still succeed once a VALID branch is used, proving
    # the refusal above did not half-write a sale row.
    conn = get_retail_conn()
    n = conn.execute("SELECT COUNT(*) FROM sales WHERE company_id=?", (company_id,)).fetchone()[0]
    conn.close()
    assert n == 0, "a refused cross-tenant branch_id must not leave a partial sale row"


# ═════════════════════════════════════════════════════════════════════════════
# 5. A pin whose uid resolves to no local branch does NOT silently file
#    under branch 1 -- it falls back, but the fallback is surfaced.
# ═════════════════════════════════════════════════════════════════════════════

def test_unresolved_pin_falls_back_but_is_surfaced(company):
    company_id, client = company
    b1_id, _ = _create_branch(client, 'Only Store')
    pid, _ = _create_product(client, initial_stock=0)
    _seed_stock(client, pid, b1_id, 10)

    # Set directly, bypassing the POST route's own uid-must-resolve
    # validation -- simulating a pin that was valid when set (e.g. before a
    # local DB reseed, or a branch row not yet pulled by sync) and no
    # longer resolves to any local branch. The POST route itself refuses to
    # WRITE an unresolvable pin (see test 4-adjacent coverage in
    # retail_route_capability_matrix_test.py's style); this is the
    # READ-side condition _resolve_working_branch must handle regardless of
    # how the pin got into that state.
    ghost_uid = str(uuid.uuid4())
    _onb.set_device_branch_uid(ghost_uid)

    before = _audit_count(company_id, 'BRANCH_PIN_UNRESOLVED')
    sale = _sale(client, pid, 1)
    assert sale.status_code == 200, sale.get_json()
    sale_id = sale.get_json()['data']['id']

    assert _sale_branch(sale_id) == b1_id, "an unresolved pin must still fall back to this device's default branch"
    after = _audit_count(company_id, 'BRANCH_PIN_UNRESOLVED')
    assert after == before + 1, "an unresolved pin must be SURFACED (audited), not silently discarded"


# ═════════════════════════════════════════════════════════════════════════════
# 6. UID NOT ID: a pin keeps working when the same branch has a DIFFERENT
#    integer id than the one it had when pinned.
# ═════════════════════════════════════════════════════════════════════════════

def test_pin_survives_the_branch_getting_a_different_local_id(company):
    company_id, client = company
    # A DECOY branch, created FIRST so it holds the LOWEST id for this
    # company -- i.e. the one `_default_branch`'s tier-3 self-heal (and
    # therefore _resolve_working_branch's own fallback tier) would return
    # if uid-based resolution silently broke and fell through. Without this
    # decoy, a single-branch company would let a BROKEN (id-based, not
    # uid-based) resolution coincidentally land on the right answer anyway
    # -- proven directly: this is the exact gap M2's own mutation proof
    # found in this test's first draft (it fell through to the fallback
    # tier, which was still correct with only one branch in the company).
    decoy_id, _ = _create_branch(client, 'Decoy Store')
    old_id, branch_uid = _create_branch(client, 'Relocating Store')
    assert decoy_id < old_id
    pid, _ = _create_product(client, initial_stock=0)
    _seed_stock(client, pid, old_id, 10)
    _seed_stock(client, pid, decoy_id, 999)  # must stay untouched throughout

    assert _pin(client, branch_uid).status_code == 200

    # Simulate the SAME branch (same uid) carrying a DIFFERENT local id --
    # e.g. this device re-seeded and the row came back re-numbered. A
    # fresh row, not an UPDATE of the primary key: this is what "the same
    # physical branch, a different device/point in time" actually looks
    # like on disk, matching the plan's own instruction to simulate by
    # writing the branch row with a different local id and the same uid.
    conn = get_retail_conn()
    conn.execute("BEGIN IMMEDIATE")
    old_row = conn.execute(
        "SELECT company_id, name, address, phone, uid, status FROM branches WHERE id=?", (old_id,)
    ).fetchone()
    new_id = old_id + 5000  # guaranteed unused: branches.id only ever grows
    # DELETE before INSERT: `uid` carries a UNIQUE constraint, so the old and
    # new rows can never coexist even for one statement.
    conn.execute("DELETE FROM branches WHERE id=?", (old_id,))
    conn.execute(
        "INSERT INTO branches (id, company_id, name, address, phone, uid, status) VALUES (?,?,?,?,?,?,?)",
        (new_id, old_row['company_id'], old_row['name'], old_row['address'],
         old_row['phone'], old_row['uid'], old_row['status']),
    )
    conn.execute("DELETE FROM inventory_balances WHERE branch_id=?", (old_id,))
    conn.execute(
        "INSERT INTO inventory_balances (company_id, product_id, branch_id, quantity_on_hand) VALUES (?,?,?,?)",
        (company_id, pid, new_id, 10),
    )
    conn.commit()
    conn.close()

    # The pin (config.json's branch_uid) was never touched -- still the
    # SAME uid string. If resolution were keyed on the integer id it would
    # now point at nothing (old_id no longer exists).
    assert _get_pin(client)['branch_uid'] == branch_uid
    assert _get_pin(client)['branch_id'] == new_id, "the read side must re-resolve the uid to the NEW local id"

    sale = _sale(client, pid, 4)
    assert sale.status_code == 200, sale.get_json()
    sale_id = sale.get_json()['data']['id']

    assert _sale_branch(sale_id) == new_id, "the pin must keep resolving to the branch's CURRENT local id"
    assert _balance(company_id, pid, new_id) == 6.0
    assert _balance(company_id, pid, decoy_id) == 999, (
        "the decoy (this company's actual _default_branch fallback answer) must stay untouched -- "
        "if it moved, resolution silently fell through instead of following the uid"
    )


# ═════════════════════════════════════════════════════════════════════════════
# 7. CLINIC-SHAPED CONFIG: a config.json with no branch_uid key round-trips
#    through get/set of OTHER keys unchanged.
# ═════════════════════════════════════════════════════════════════════════════

def test_config_without_branch_uid_key_round_trips_unchanged():
    """Shared runtime: Clinic imports onboarding_routes.py too but calls
    neither get_device_branch_uid nor set_device_branch_uid anywhere. This
    proves a config.json written by code that has never heard of
    branch_uid -- the Clinic shape -- is left alone by both functions."""
    original = _onb._read_config()
    try:
        _onb._write_config({'some_other_key': 'clinic-value', 'another': 42})
        cfg0 = _onb._read_config()
        assert cfg0.get('some_other_key') == 'clinic-value'
        assert cfg0.get('another') == 42

        assert _onb.get_device_branch_uid() is None, "no branch_uid key was ever written -- must read back as None, never raise"

        _onb.set_device_branch_uid('a-real-uid')
        cfg1 = _onb._read_config()
        assert cfg1['some_other_key'] == 'clinic-value', "setting the pin must not disturb an unrelated key"
        assert cfg1['another'] == 42
        assert cfg1['branch_uid'] == 'a-real-uid'

        _onb.set_device_branch_uid(None)
        cfg2 = _onb._read_config()
        assert cfg2['some_other_key'] == 'clinic-value', "clearing the pin must not disturb an unrelated key"
        assert cfg2['another'] == 42
        assert _onb.get_device_branch_uid() is None
    finally:
        # Restore exactly what was on disk before this test, so later tests
        # in this module (and the autouse pin-reset fixture) see the state
        # they expect.
        p = _onb._config_path()
        import json as _json
        with open(p, 'w', encoding='utf-8') as f:
            _json.dump(original, f, indent=4)
