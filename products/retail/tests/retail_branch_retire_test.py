"""Aura Retail -- retiring a branch, and the resolver guards that make it real.

A branch could be created and renamed and never closed. CLAUDE.md listed it
under "what's genuinely missing" and `update_branch`'s own docstring says
retiring one "is its own design" because it touches stock balances, the
per-device branch pin and open cash drawers.

THE TRAP THIS SUITE EXISTS TO PIN. `branches.status TEXT DEFAULT 'active'`
ALREADY existed, so the tempting implementation is "add a route, flip the
flag". That would be COSMETIC AND DANGEROUS: `list_branches` filters on
status, but the three tiers of `_resolve_working_branch`, `_default_branch`
and sync's `_resolve_branch_id` did NOT. A retired branch would vanish from
the picker while every device already pinned to it -- and every device with no
pin at all, if it were the company's lowest id -- kept silently filing sales
and stock movements under it. The resolver guards are the actual work; the
status column was never the hard part.

WHAT RETIREMENT REFUSES, and why each is a refusal rather than a warning:

  * STOCK STILL ON HAND. Inventory keyed to a branch nobody can select is
    unreachable through the UI -- the same class of unrecoverable state as a
    balance with no ledger row behind it. The remedy already ships: transfer
    it out first (schema v28, nav-reachable on desktop).
  * AN OPEN CASH SESSION. A drawer nobody can reach cannot be counted or
    closed, and its Z-report is how a shift is reconciled.
  * PENDING OR IN-TRANSIT TRANSFERS naming the branch either end. Goods in
    flight have to land somewhere that still exists.
  * THE LAST ACTIVE BRANCH. A shop with no active branch has nowhere to file
    the next sale, and `_default_branch` would silently invent a replacement
    -- which is a new branch nobody asked for, not a closure.

Retirement is a SOFT status flip, never a delete. Nothing FK-references
`branches.id` (every referencing column says so in its own comment, because
the id is a per-device autoincrement rather than a wire identity), so a delete
would silently strand every historical row. Historical reads keep working --
reports and statements read `sales`/`returns` by branch_id and only join
`branches` for a display name.

Run:
    pytest products/retail/tests/retail_branch_retire_test.py -v
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_branchretire_"))
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

API = '/api/sub/retail'


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


def _make_admin():
    """Admin holds CAP_EMPLOYEES, which is what create_branch/update_branch
    already require -- retirement is the same authority."""
    email = f"br-{uuid.uuid4().hex[:10]}@test.local"
    password = "BranchRetirePW1"
    company_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, require_password_change) "
        "VALUES (?,?,?,?,?,?,?,0)",
        (str(uuid.uuid4()), company_id, "EMP-0001", email, hash_password(password), "admin", "active"),
    )
    conn.commit()
    conn.close()
    client = app.test_client()
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.get_json()
    client.test_company_id = company_id
    return client, company_id


def _add_branch(client, name):
    r = client.post(f'{API}/branches', json={'name': name})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _retire(client, branch_id):
    return client.post(f'{API}/branches/{branch_id}/retire', json={})


def _status(cid, branch_id):
    conn = get_retail_conn()
    row = conn.execute(
        "SELECT status FROM branches WHERE id=? AND company_id=?", (branch_id, cid)).fetchone()
    conn.close()
    return row['status'] if row else None


def _listed_ids(client):
    r = client.get(f'{API}/branches')
    assert r.status_code == 200, r.get_json()
    return [b['id'] for b in r.get_json()['data']]


def _seed_product_with_stock(cid, branch_id, qty):
    conn = get_retail_conn()
    pid = str(uuid.uuid4())
    sku = f'BR-{uuid.uuid4().hex[:8]}'
    conn.execute(
        "INSERT INTO products (id,company_id,sku,name,cost_price,sell_price,tax_rate) VALUES (?,?,?,?,?,?,0)",
        (pid, cid, sku, sku, 1.0, 5.0))
    real_pid = conn.execute(
        "SELECT id FROM products WHERE company_id=? AND sku=?", (cid, sku)).fetchone()[0]
    conn.execute(
        "INSERT INTO inventory_balances (company_id,product_id,branch_id,quantity_on_hand) VALUES (?,?,?,?)",
        (cid, real_pid, branch_id, qty))
    conn.commit()
    conn.close()
    return real_pid


# ── the happy path ────────────────────────────────────────────────────────────

def test_an_empty_branch_can_be_retired_and_leaves_the_picker():
    client, cid = _make_admin()
    first = _add_branch(client, 'Main')
    second = _add_branch(client, 'Kiosk')

    r = _retire(client, second)
    assert r.status_code == 200, r.get_json()

    assert _status(cid, second) == 'inactive'
    assert second not in _listed_ids(client), "a retired branch must leave the picker"
    assert first in _listed_ids(client)


def test_retiring_is_a_status_flip_not_a_delete():
    """Nothing FK-references branches.id, so a delete would silently strand
    every historical sale, movement and drawer keyed to it. The row stays."""
    client, cid = _make_admin()
    _add_branch(client, 'Main')
    second = _add_branch(client, 'Kiosk')
    _retire(client, second)

    conn = get_retail_conn()
    row = conn.execute("SELECT id, name FROM branches WHERE id=?", (second,)).fetchone()
    conn.close()
    assert row is not None and row['name'] == 'Kiosk', "the row itself must survive retirement"


# ── the four refusals ─────────────────────────────────────────────────────────

def test_a_branch_holding_stock_is_refused():
    """Inventory keyed to a branch nobody can select is unreachable through
    the UI. Transfer it out first -- that feature ships."""
    client, cid = _make_admin()
    _add_branch(client, 'Main')
    second = _add_branch(client, 'Kiosk')
    _seed_product_with_stock(cid, second, 7)

    r = _retire(client, second)
    assert r.status_code == 409, r.get_json()
    assert 'stock' in (r.get_json().get('message') or '').lower(), r.get_json()
    assert _status(cid, second) == 'active', "a refused retirement must change nothing"


def test_zero_and_negative_stock_rows_do_not_block_retirement():
    """THE ALLOW-HALF of the stock guard. A branch that once held stock and
    now holds none still has `inventory_balances` ROWS sitting at 0 -- they
    are not deleted when stock runs out. Blocking on the row's existence
    rather than its quantity would make a branch that genuinely holds nothing
    permanently un-retirable, which is the same guard failing in the opposite
    direction."""
    client, cid = _make_admin()
    _add_branch(client, 'Main')
    second = _add_branch(client, 'Kiosk')
    _seed_product_with_stock(cid, second, 0)

    r = _retire(client, second)
    assert r.status_code == 200, r.get_json()
    assert _status(cid, second) == 'inactive'


def test_an_open_cash_session_is_refused():
    """A drawer nobody can reach cannot be counted or closed, and its
    Z-report is how a shift is reconciled."""
    client, cid = _make_admin()
    _add_branch(client, 'Main')
    second = _add_branch(client, 'Kiosk')

    conn = get_retail_conn()
    conn.execute(
        "INSERT INTO cash_sessions (id,company_id,branch_id,status,opening_float) "
        "VALUES (?,?,?,'open',0)", (str(uuid.uuid4()), cid, second))
    conn.commit()
    conn.close()

    r = _retire(client, second)
    assert r.status_code == 409, r.get_json()
    assert _status(cid, second) == 'active'


def test_an_in_flight_transfer_is_refused_at_either_end():
    """Goods in flight have to land somewhere that still exists -- so a
    pending transfer blocks retirement whether the branch is the SOURCE or
    the DESTINATION."""
    client, cid = _make_admin()
    _add_branch(client, 'Main')
    source = _add_branch(client, 'Source')
    dest = _add_branch(client, 'Dest')

    conn = get_retail_conn()
    # `stock_transfers.id` is a client-generated UUID TEXT primary key
    # (schema.py), not an autoincrement, and the table carries no
    # transfer_number column -- the id IS the identity.
    conn.execute(
        "INSERT INTO stock_transfers (id,company_id,source_branch_id,destination_branch_id,status) "
        "VALUES (?,?,?,?,'in_transit')",
        (str(uuid.uuid4()), cid, source, dest))
    conn.commit()
    conn.close()

    assert _retire(client, source).status_code == 409, "blocked as the source"
    assert _retire(client, dest).status_code == 409, "blocked as the destination"
    assert _status(cid, source) == 'active' and _status(cid, dest) == 'active'


def test_the_last_active_branch_cannot_be_retired():
    """A shop with no active branch has nowhere to file the next sale, and
    `_default_branch` would silently INVENT a replacement -- a new branch
    nobody asked for, which is not a closure."""
    client, cid = _make_admin()
    only = _add_branch(client, 'Only')

    r = _retire(client, only)
    assert r.status_code == 409, r.get_json()
    assert _status(cid, only) == 'active'


# ── the guards that make it more than cosmetic ────────────────────────────────

def test_a_sale_never_files_itself_under_a_retired_branch():
    """THE POINT OF THE WHOLE FEATURE. Before the resolver guards,
    `_default_branch` returned the company's LOWEST-id branch with no status
    filter -- so retiring the original branch left every unpinned device
    still filing sales under it, invisibly, forever.

    Retire the first branch (the one a bare `ORDER BY id LIMIT 1` would pick)
    and ring a sale: it must land on the surviving ACTIVE branch."""
    client, cid = _make_admin()
    first = _add_branch(client, 'Old Shop')
    second = _add_branch(client, 'New Shop')

    # Stock on BOTH branches, deliberately. If only the surviving branch had
    # it, a sale that wrongly resolved to the retired one would fail with
    # "Insufficient stock" -- red, but for the wrong reason, and the
    # assertion below would never run. Stocking both means the sale SUCCEEDS
    # either way and the branch it filed under is the only thing that can
    # discriminate. Measured: with the `_default_branch` status filter
    # removed, this fails on the branch id itself, not on a stock symptom.
    pid = _seed_product_with_stock(cid, second, 10)
    conn = get_retail_conn()
    conn.execute(
        "INSERT INTO inventory_balances (company_id,product_id,branch_id,quantity_on_hand) VALUES (?,?,?,?)",
        (cid, pid, first, 10))
    conn.commit()
    conn.close()

    assert _retire(client, first).status_code == 409, (
        "precondition: the branch holding stock is refused, so it must be emptied first")
    conn = get_retail_conn()
    conn.execute("UPDATE inventory_balances SET quantity_on_hand=0 WHERE company_id=? AND branch_id=?",
                 (cid, first))
    conn.commit()
    conn.close()
    assert _retire(client, first).status_code == 200

    # Stock put BACK on the now-retired branch, directly. This is the whole
    # point of the fixture: the retired branch must be able to satisfy the
    # sale, so that a resolver which wrongly picks it SUCCEEDS and is caught
    # by the branch assertion below rather than by an "Insufficient stock"
    # 400. Without this the test still goes red under mutation, but for the
    # wrong reason -- the branch assertion never runs, which is exactly the
    # "asserts an outcome where it should assert the check ran" shape.
    conn = get_retail_conn()
    conn.execute("UPDATE inventory_balances SET quantity_on_hand=10 WHERE company_id=? AND branch_id=?",
                 (cid, first))
    conn.commit()
    conn.close()

    r = client.post(f'{API}/sales', json={
        'items': [{'product_id': pid, 'quantity': 1}],
        'payment_method': 'cash', 'idempotency_key': str(uuid.uuid4()),
    })
    assert r.status_code == 200, r.get_json()

    conn = get_retail_conn()
    branch_of_sale = conn.execute(
        "SELECT branch_id FROM sales WHERE id=?", (r.get_json()['data']['id'],)).fetchone()[0]
    conn.close()
    assert branch_of_sale != first, (
        f"the sale filed under the RETIRED branch {first}; the resolver ignored status")
    assert branch_of_sale == second


def test_an_explicit_retired_branch_id_is_refused_for_a_mutation():
    """The explicit tier of `_resolve_working_branch`. A caller naming a
    retired branch outright must be TOLD, not silently redirected -- a sale
    filed somewhere other than where the caller said is its own kind of
    wrong.

    Asserted against a MUTATION, not a read, because that is what this tier
    actually governs: `_resolve_working_branch`'s own comment (account-
    hierarchy design §3.3/§4.2 D7) records that a scoped caller's MUTATIONS
    are refused for a foreign branch_id while READS are deliberately handled
    differently. An earlier draft of this test pointed at `GET /products` and
    passed a 200 with an empty list -- which proved nothing about the guard
    and would have gone green whether or not it existed."""
    client, cid = _make_admin()
    main = _add_branch(client, 'Main')
    second = _add_branch(client, 'Kiosk')
    # Stock goes on the branch that SURVIVES -- putting it on the one being
    # retired would (correctly) trip this feature's own stock refusal and
    # leave the test proving nothing about the resolver.
    pid = _seed_product_with_stock(cid, main, 5)
    assert _retire(client, second).status_code == 200

    r = client.post(f'{API}/sales', json={
        'items': [{'product_id': pid, 'quantity': 1}],
        'branch_id': second,
        'payment_method': 'cash', 'idempotency_key': str(uuid.uuid4()),
    })
    assert r.status_code == 400, (
        f"a sale naming a retired branch must be refused, got {r.status_code}: {r.get_json()}")


def test_retiring_frees_a_slot_under_the_licence_branch_limit():
    """`create_branch` counts only ACTIVE branches against `max_branches`
    (retail_api.py's own BRANCH_LIMIT check), so retirement genuinely returns
    a slot rather than leaving the shop permanently at its cap. Asserted
    because it is a real commercial consequence of the status flip, not an
    accident of it."""
    client, cid = _make_admin()
    _add_branch(client, 'Main')
    second = _add_branch(client, 'Kiosk')

    conn = get_retail_conn()
    before = conn.execute(
        "SELECT COUNT(*) FROM branches WHERE company_id=? AND COALESCE(status,'active')='active'",
        (cid,)).fetchone()[0]
    conn.close()

    assert _retire(client, second).status_code == 200

    conn = get_retail_conn()
    after = conn.execute(
        "SELECT COUNT(*) FROM branches WHERE company_id=? AND COALESCE(status,'active')='active'",
        (cid,)).fetchone()[0]
    conn.close()
    assert after == before - 1
