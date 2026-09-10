"""
Aura Retail -- inter-branch stock transfers (launch-readiness, schema v28).

ROADMAP.md's 2026-08-31 "retail v28 CLAIMED for inter-branch transfers" entry
and database/schema.py's `_migrate_add_stock_transfers` docstring specify the
two-phase design this file pins against the real HTTP routes in
api/retail_api.py's "Stock Transfers" section:

    CREATE (pending, no stock moves) -> SEND (stock leaves source, in_transit)
        -> RECEIVE (stock arrives at destination, received) -- or CANCEL
        while still pending.

Boots the real Flask app against a throwaway temp AURA_APP_DATA directory,
same bootstrap shape as retail_pricing_test.py / retail_route_capability_
matrix_test.py, and drives the routes exactly as a real client would --
inventory_balances is read back directly only to VERIFY what the routes
claim to have done, never to set up a shortcut around them.

Run:
    pytest products/retail/tests/retail_stock_transfer_test.py -v
"""
import os
import random
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

PRODUCT_DIR = Path(__file__).resolve().parents[1]        # products/retail
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent                    # aura-fullsuits
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_stocktransfer_"))
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


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


# ── Shop bootstrap ────────────────────────────────────────────────────────────

def _make_user(role, company_id, *, email_prefix='xfer'):
    """A real account, seeded through the SAME production helper account
    creation uses -- matching retail_route_capability_matrix_test.py's own
    `_make_user`, so a cashier here has exactly the grants a real cashier
    account gets, not a hand-picked subset."""
    email = f"{email_prefix}-{role}-{uuid.uuid4().hex[:10]}@test.local"
    password = "StockXferPW1"  # pragma: allowlist secret -- throwaway fixture value
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

    client = app.test_client()
    r = client.post('/api/auth/login', json={'email': email, 'password': password})
    assert r.status_code == 200, r.get_json()
    return client


def _make_shop():
    """One company, an admin, TWO branches, and one product stocked 100 at
    the FIRST branch only -- the second branch starts genuinely empty, so a
    transfer landing stock there is a real, observable change rather than a
    no-op against an already-nonzero balance."""
    company_id = str(uuid.uuid4())
    admin = _make_user('admin', company_id)
    admin.get('/api/sub/retail/settings/tax')  # warms _ensure_credit_schema / doc_sequences

    dconn = get_retail_conn()
    dconn.execute(
        "INSERT OR REPLACE INTO doc_sequences (company_id,doc_type,last_no) VALUES (?,'sale',?)",
        (company_id, random.randint(1, 5_000_000)),
    )
    dconn.execute("INSERT INTO branches (company_id,name) VALUES (?,'Warehouse')", (company_id,))
    dconn.execute("INSERT INTO branches (company_id,name) VALUES (?,'Storefront')", (company_id,))
    branch_rows = dconn.execute(
        "SELECT id FROM branches WHERE company_id=? ORDER BY id", (company_id,)
    ).fetchall()
    source_bid, dest_bid = branch_rows[0]['id'], branch_rows[1]['id']

    product_id = str(uuid.uuid4())
    dconn.execute(
        "INSERT INTO products (id,company_id,sku,name,cost_price,sell_price,tax_rate,status) "
        "VALUES (?,?,?,?,?,?,?,'active')",
        (product_id, company_id, f'XFER-{uuid.uuid4().hex[:6]}', 'Transferable Widget', 5, 10, 0),
    )
    dconn.execute(
        "INSERT INTO inventory_balances (company_id,product_id,branch_id,quantity_on_hand) VALUES (?,?,?,100)",
        (company_id, product_id, source_bid),
    )
    dconn.commit()
    dconn.close()
    return admin, company_id, source_bid, dest_bid, product_id


def _on_hand(company_id, product_id, branch_id):
    conn = get_retail_conn()
    row = conn.execute(
        "SELECT quantity_on_hand FROM inventory_balances WHERE company_id=? AND product_id=? AND branch_id=?",
        (company_id, product_id, branch_id),
    ).fetchone()
    conn.close()
    return float(row['quantity_on_hand']) if row else 0.0


def _transfer_status(transfer_id):
    conn = get_retail_conn()
    row = conn.execute("SELECT status FROM stock_transfers WHERE id=?", (transfer_id,)).fetchone()
    conn.close()
    return row['status'] if row else None


# ── Route helpers ─────────────────────────────────────────────────────────────

def _create(client, source_bid, dest_bid, product_id, qty):
    return client.post('/api/sub/retail/stock-transfers', json={
        'source_branch_id': source_bid, 'destination_branch_id': dest_bid,
        'items': [{'product_id': product_id, 'quantity': qty}],
    })


def _send(client, transfer_id):
    return client.post(f'/api/sub/retail/stock-transfers/{transfer_id}/send')


def _receive(client, transfer_id, item_id, qty_received):
    return client.post(f'/api/sub/retail/stock-transfers/{transfer_id}/receive', json={
        'items': [{'id': item_id, 'quantity_received': qty_received}],
    })


def _cancel(client, transfer_id):
    return client.post(f'/api/sub/retail/stock-transfers/{transfer_id}/cancel')


def _get(client, transfer_id):
    return client.get(f'/api/sub/retail/stock-transfers/{transfer_id}')


def _first_item_id(client, transfer_id):
    r = _get(client, transfer_id)
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['items'][0]['id']


# ═════════════════════════════════════════════════════════════════════════════
# Happy path: create -> send -> receive, stock lands where it should at each step
# ═════════════════════════════════════════════════════════════════════════════

def test_create_moves_no_stock_at_all():
    admin, cid, source_bid, dest_bid, pid = _make_shop()
    r = _create(admin, source_bid, dest_bid, pid, 10)
    assert r.status_code == 200, r.get_json()
    transfer_id = r.get_json()['data']['id']

    assert _transfer_status(transfer_id) == 'pending'
    assert _on_hand(cid, pid, source_bid) == 100.0, "create must not touch the source balance"
    assert _on_hand(cid, pid, dest_bid) == 0.0, "create must not touch the destination balance"


def test_send_decrements_source_not_create():
    """Named explicitly per the task's own checklist: source stock changes on
    SEND, never on CREATE."""
    admin, cid, source_bid, dest_bid, pid = _make_shop()
    transfer_id = _create(admin, source_bid, dest_bid, pid, 10).get_json()['data']['id']
    assert _on_hand(cid, pid, source_bid) == 100.0  # unchanged right after create

    r = _send(admin, transfer_id)
    assert r.status_code == 200, r.get_json()
    assert _transfer_status(transfer_id) == 'in_transit'
    assert _on_hand(cid, pid, source_bid) == 90.0
    assert _on_hand(cid, pid, dest_bid) == 0.0, "SEND must not credit the destination yet -- goods are in transit"


def test_receive_increments_destination_not_send():
    """Named explicitly per the task's own checklist: destination stock
    changes on RECEIVE, never on SEND."""
    admin, cid, source_bid, dest_bid, pid = _make_shop()
    transfer_id = _create(admin, source_bid, dest_bid, pid, 10).get_json()['data']['id']
    _send(admin, transfer_id)
    item_id = _first_item_id(admin, transfer_id)
    assert _on_hand(cid, pid, dest_bid) == 0.0  # unchanged right after send

    r = _receive(admin, transfer_id, item_id, 10)
    assert r.status_code == 200, r.get_json()
    assert _transfer_status(transfer_id) == 'received'
    assert _on_hand(cid, pid, dest_bid) == 10.0
    assert _on_hand(cid, pid, source_bid) == 90.0, "RECEIVE must not touch the source balance a second time"


def test_full_happy_path_end_to_end():
    admin, cid, source_bid, dest_bid, pid = _make_shop()
    transfer_id = _create(admin, source_bid, dest_bid, pid, 10).get_json()['data']['id']
    assert _send(admin, transfer_id).status_code == 200
    item_id = _first_item_id(admin, transfer_id)
    r = _receive(admin, transfer_id, item_id, 10)
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['data']['shortages'] == []

    data = _get(admin, transfer_id).get_json()['data']
    assert data['transfer']['status'] == 'received'
    assert data['transfer']['sent_at'] is not None
    assert data['transfer']['received_at'] is not None
    assert data['items'][0]['quantity_sent'] == 10
    assert data['items'][0]['quantity_received'] == 10
    assert _on_hand(cid, pid, source_bid) == 90.0
    assert _on_hand(cid, pid, dest_bid) == 10.0


# ═════════════════════════════════════════════════════════════════════════════
# Shortage: send 6, receive 5 -- both numbers survive, destination lands on 5
# ═════════════════════════════════════════════════════════════════════════════

def test_shortage_is_recorded_as_fact_not_refused():
    admin, cid, source_bid, dest_bid, pid = _make_shop()
    transfer_id = _create(admin, source_bid, dest_bid, pid, 6).get_json()['data']['id']
    assert _send(admin, transfer_id).status_code == 200
    item_id = _first_item_id(admin, transfer_id)

    r = _receive(admin, transfer_id, item_id, 5)
    assert r.status_code == 200, r.get_json()  # NOT an error -- see the route's own docstring
    assert _transfer_status(transfer_id) == 'received'

    shortages = r.get_json()['data']['shortages']
    assert len(shortages) == 1
    assert shortages[0]['product_id'] == pid
    assert shortages[0]['quantity_sent'] == 6
    assert shortages[0]['quantity_received'] == 5

    # Both numbers survive on the line itself -- quantity_sent is never
    # overwritten by the reconciled figure.
    data = _get(admin, transfer_id).get_json()['data']
    assert data['items'][0]['quantity_sent'] == 6
    assert data['items'][0]['quantity_received'] == 5

    # Stock at the destination is exactly what arrived, not what was sent.
    assert _on_hand(cid, pid, dest_bid) == 5.0
    assert _on_hand(cid, pid, source_bid) == 94.0  # the full 6 already left on SEND


def test_zero_received_is_a_real_fact_not_an_unset_marker():
    """0 must move no stock and write no movement, but must still let the
    transfer land on 'received' -- matching the migration docstring's NULL
    (unset) vs 0 (checked, none arrived) distinction."""
    admin, cid, source_bid, dest_bid, pid = _make_shop()
    transfer_id = _create(admin, source_bid, dest_bid, pid, 4).get_json()['data']['id']
    _send(admin, transfer_id)
    item_id = _first_item_id(admin, transfer_id)

    r = _receive(admin, transfer_id, item_id, 0)
    assert r.status_code == 200, r.get_json()
    assert _transfer_status(transfer_id) == 'received'
    assert _on_hand(cid, pid, dest_bid) == 0.0
    assert r.get_json()['data']['shortages'][0]['quantity_received'] == 0


# ═════════════════════════════════════════════════════════════════════════════
# Illegal transitions -- refused, never a 500, and never move stock twice
# ═════════════════════════════════════════════════════════════════════════════

def test_double_send_is_refused_and_does_not_double_decrement():
    admin, cid, source_bid, dest_bid, pid = _make_shop()
    transfer_id = _create(admin, source_bid, dest_bid, pid, 10).get_json()['data']['id']
    assert _send(admin, transfer_id).status_code == 200
    assert _on_hand(cid, pid, source_bid) == 90.0

    r = _send(admin, transfer_id)
    assert r.status_code == 409, r.get_json()
    assert _on_hand(cid, pid, source_bid) == 90.0, "a refused second SEND must not decrement stock again"
    assert _transfer_status(transfer_id) == 'in_transit'


def test_receive_before_send_is_refused():
    admin, cid, source_bid, dest_bid, pid = _make_shop()
    transfer_id = _create(admin, source_bid, dest_bid, pid, 10).get_json()['data']['id']
    item_id = _first_item_id(admin, transfer_id)

    r = _receive(admin, transfer_id, item_id, 10)
    assert r.status_code == 409, r.get_json()
    assert _on_hand(cid, pid, source_bid) == 100.0
    assert _on_hand(cid, pid, dest_bid) == 0.0
    assert _transfer_status(transfer_id) == 'pending'


def test_cancel_after_send_is_refused():
    admin, cid, source_bid, dest_bid, pid = _make_shop()
    transfer_id = _create(admin, source_bid, dest_bid, pid, 10).get_json()['data']['id']
    assert _send(admin, transfer_id).status_code == 200

    r = _cancel(admin, transfer_id)
    assert r.status_code == 409, r.get_json()
    assert _transfer_status(transfer_id) == 'in_transit'
    assert _on_hand(cid, pid, source_bid) == 90.0  # unchanged by the refused cancel


def test_cancel_after_receive_is_refused():
    admin, cid, source_bid, dest_bid, pid = _make_shop()
    transfer_id = _create(admin, source_bid, dest_bid, pid, 10).get_json()['data']['id']
    _send(admin, transfer_id)
    item_id = _first_item_id(admin, transfer_id)
    _receive(admin, transfer_id, item_id, 10)

    r = _cancel(admin, transfer_id)
    assert r.status_code == 409, r.get_json()
    assert _transfer_status(transfer_id) == 'received'


def test_double_receive_is_refused_and_does_not_double_credit():
    admin, cid, source_bid, dest_bid, pid = _make_shop()
    transfer_id = _create(admin, source_bid, dest_bid, pid, 10).get_json()['data']['id']
    _send(admin, transfer_id)
    item_id = _first_item_id(admin, transfer_id)
    assert _receive(admin, transfer_id, item_id, 10).status_code == 200
    assert _on_hand(cid, pid, dest_bid) == 10.0

    r = _receive(admin, transfer_id, item_id, 10)
    assert r.status_code == 409, r.get_json()
    assert _on_hand(cid, pid, dest_bid) == 10.0, "a refused second RECEIVE must not credit stock again"


def test_cancel_pending_is_allowed_and_touches_no_stock():
    admin, cid, source_bid, dest_bid, pid = _make_shop()
    transfer_id = _create(admin, source_bid, dest_bid, pid, 10).get_json()['data']['id']
    r = _cancel(admin, transfer_id)
    assert r.status_code == 200, r.get_json()
    assert _transfer_status(transfer_id) == 'cancelled'
    assert _on_hand(cid, pid, source_bid) == 100.0
    assert _on_hand(cid, pid, dest_bid) == 0.0

    # And a cancelled transfer cannot then be sent.
    assert _send(admin, transfer_id).status_code == 409


# ═════════════════════════════════════════════════════════════════════════════
# Branch validation
# ═════════════════════════════════════════════════════════════════════════════

def test_source_equals_destination_is_refused():
    admin, cid, source_bid, _dest_bid, pid = _make_shop()
    r = _create(admin, source_bid, source_bid, pid, 5)
    assert r.status_code == 400, r.get_json()

    conn = get_retail_conn()
    count = conn.execute("SELECT COUNT(*) FROM stock_transfers WHERE company_id=?", (cid,)).fetchone()[0]
    conn.close()
    assert count == 0, "a refused create must not write a transfer row"


def test_a_branch_from_another_company_is_refused():
    admin, cid, source_bid, _dest_bid, pid = _make_shop()
    _other_admin, _other_cid, other_source, other_dest, _other_pid = _make_shop()

    r = _create(admin, source_bid, other_dest, pid, 5)
    assert r.status_code == 400, r.get_json()

    r2 = _create(admin, other_source, source_bid, pid, 5)
    assert r2.status_code == 400, r2.get_json()

    conn = get_retail_conn()
    count = conn.execute("SELECT COUNT(*) FROM stock_transfers WHERE company_id=?", (cid,)).fetchone()[0]
    conn.close()
    assert count == 0


# ═════════════════════════════════════════════════════════════════════════════
# Overselling at SEND: refused hard (adjust_stock's posture, not create_sale's)
# ═════════════════════════════════════════════════════════════════════════════

def test_send_refuses_to_oversell_the_source_branch():
    admin, cid, source_bid, dest_bid, pid = _make_shop()
    transfer_id = _create(admin, source_bid, dest_bid, pid, 500).get_json()['data']['id']  # only 100 on hand

    r = _send(admin, transfer_id)
    assert r.status_code == 400, r.get_json()
    assert _on_hand(cid, pid, source_bid) == 100.0, "a refused SEND must not touch the source balance"
    assert _transfer_status(transfer_id) == 'pending', "a refused SEND must not advance the status"


# ═════════════════════════════════════════════════════════════════════════════
# Capability: without CAP_STOCK_ADJUST, every mutation is refused and no
# stock moves
# ═════════════════════════════════════════════════════════════════════════════

def test_cashier_cannot_create_a_transfer():
    admin, cid, source_bid, dest_bid, pid = _make_shop()
    cashier = _make_user('cashier', cid)

    r = _create(cashier, source_bid, dest_bid, pid, 10)
    assert r.status_code == 403, r.get_json()

    conn = get_retail_conn()
    count = conn.execute("SELECT COUNT(*) FROM stock_transfers WHERE company_id=?", (cid,)).fetchone()[0]
    conn.close()
    assert count == 0
    assert _on_hand(cid, pid, source_bid) == 100.0


def test_cashier_cannot_send_send_receive_or_cancel_an_existing_transfer():
    admin, cid, source_bid, dest_bid, pid = _make_shop()
    cashier = _make_user('cashier', cid)
    transfer_id = _create(admin, source_bid, dest_bid, pid, 10).get_json()['data']['id']
    item_id = _first_item_id(admin, transfer_id)

    r_send = _send(cashier, transfer_id)
    assert r_send.status_code == 403, r_send.get_json()
    assert _transfer_status(transfer_id) == 'pending'
    assert _on_hand(cid, pid, source_bid) == 100.0

    r_cancel = _cancel(cashier, transfer_id)
    assert r_cancel.status_code == 403, r_cancel.get_json()
    assert _transfer_status(transfer_id) == 'pending'

    # Now really send it (as the admin) so RECEIVE has something to refuse.
    assert _send(admin, transfer_id).status_code == 200
    r_receive = _receive(cashier, transfer_id, item_id, 10)
    assert r_receive.status_code == 403, r_receive.get_json()
    assert _transfer_status(transfer_id) == 'in_transit'
    assert _on_hand(cid, pid, dest_bid) == 0.0, "a refused RECEIVE must not credit stock"


def test_a_manager_may_run_the_whole_transfer_lifecycle():
    """CAP_STOCK_ADJUST is a manager-and-above authority (same tier as
    adjust_stock/create_purchase_order) -- a manager, not just an admin,
    must be able to complete the whole flow."""
    admin, cid, source_bid, dest_bid, pid = _make_shop()
    manager = _make_user('manager', cid)

    transfer_id = _create(manager, source_bid, dest_bid, pid, 10).get_json()['data']['id']
    assert _send(manager, transfer_id).status_code == 200
    item_id = _first_item_id(manager, transfer_id)
    assert _receive(manager, transfer_id, item_id, 10).status_code == 200
    assert _on_hand(cid, pid, dest_bid) == 10.0
