"""
Aura Retail -- cross-device money defect fix (sync apply-side):
`SyncService._apply_event`'s "return" branch (commercial_runtime/sync/
sync_service.py) dropped `tender_refund_amount`, `ar_forgiven_amount`,
`store_credit_amount` from its INSERT column list, so every SYNCED return
landed at the column DEFAULT of 0 regardless of what the sending device
actually recorded. `create_return`'s own partial-return safety check
(`_prior` in retail_api.py) trusts `tender_refund_amount` alone to know how
much of a sale's tender pool remains payable -- with the applied column
silently zeroed, a second device's own return against the same sale sees no
prior claim at all and reopens the WHOLE tender pool, doubling the cash
payout. Measured reproduction (also this task's own): sale 2x50=100, 40 cash
/ 60 AR; return 1 claims 40 tender; after syncing to device B with the
unfixed INSERT, return 2 on B ALSO claims 40 tender -- 80 paid out against 40
ever collected.

This file drives the REAL `SyncService._apply_event` (never a hand-simulated
row) with a realistic 'return' create-event payload -- the exact technique
retail_stock_sync_apply_hardening_test.py already established for this same
module's inventory_movement/branch branches. A bare second-device fixture
(retail_two_install_roundtrip_test.py's own `install_b`) is unnecessary here:
the point under test is the interaction between this one INSERT and
create_return's OWN, unmodified `_prior` query, and those only make sense
read against the SAME database -- so every test below applies the synced
event onto the SAME Flask app's own retail.db that create_return's real HTTP
route also writes to, exactly the shape "a return arrived here via sync, now
this till rings a second one against that sale" actually takes in
production.

Covers:
  1. A payload WITH the three split keys applies all three values exactly
     (and confirms `session_id` really is written NULL, so a synced return
     can never enter another device's own X/Z report).
  2. A payload WITHOUT them (older device) falls back to treating the WHOLE
     refund as tender -- the conservative failure direction (see
     sync_service.py's own comment on this fix: guessing 0 PERMITS a double
     payout, guessing high only REFUSES a later over-refund).
  2b. A payload carrying the keys EXPLICITLY NULL takes the identical
      fallback path as an absent key -- `dict.get(key, default)` returns
      `None`, not `default`, when `key` is present with value `None`.
  3. THE END-TO-END CONSEQUENCE (the decisive test): after a return syncs in
     with its split intact, a second, REAL return (the actual
     `/api/sub/retail/returns` route, never reached into or modified) against
     the SAME sale on the receiving device can no longer draw more tender
     than the sale ever collected. This test must go RED against the unfixed
     INSERT -- see this task's own PROOF REQUIRED section for the verbatim
     before/after numbers.

Run ONE FILE PER PROCESS (AUDIT-010):
    pytest products/retail/tests/retail_returns_sync_settlement_test.py -v
"""
import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_returns_sync_settlement_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
# AURA_SITE_RELAY_ENABLED=0 in the SAME os.environ.update call as the other
# env vars -- otherwise a licensed WINDOWS install (which this file's own
# seed_active_license call below makes this process) elects itself a LAN
# relay hub on Flask app boot and starts four threads plus a TLS listener
# nothing in this file needs (see CLAUDE.md's Sync section, "site_relay").
os.environ.update(
    AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA),
    AURA_SITE_RELAY_ENABLED="0",
)
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402
seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")
os.environ.pop("AURA_RETAIL_DEMO_MODE", None)

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402
from commercial_runtime.sync.sync_service import SyncService  # noqa: E402
from database.schema import get_retail_conn  # noqa: E402


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


def _make_admin_and_product(price=50.0, tax_rate=0.0, stock=10):
    """Identical technique to retail_returns_wave0_test.py's own helper of
    the same name (duplicated here rather than imported -- no shared
    conftest.py exists for products/retail/tests/, matching every other
    file's own stated convention)."""
    email = f"retsync-{uuid.uuid4().hex[:10]}@test.local"
    password = "ReturnsSyncPW1"
    company_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, require_password_change) "
        "VALUES (?,?,?,?,?,?,?,0)",
        (user_id, company_id, "EMP-0001", email, hash_password(password), "admin", "active"),
    )
    conn.commit()
    conn.close()

    rconn = get_retail_conn()
    rconn.execute("INSERT INTO branches (company_id,name) VALUES (?, 'Main')", (company_id,))
    branch_id = rconn.execute(
        "SELECT id FROM branches WHERE company_id=? ORDER BY id LIMIT 1", (company_id,)
    ).fetchone()[0]
    rconn.execute(
        "INSERT INTO products (id,company_id,sku,name,cost_price,sell_price,tax_rate) "
        "VALUES (?,?,'RTS-1','Return Sync Item',5,?,?)",
        (str(uuid.uuid4()), company_id, price, tax_rate),
    )
    product_id = rconn.execute(
        "SELECT id FROM products WHERE company_id=? AND sku='RTS-1'", (company_id,)
    ).fetchone()[0]
    rconn.execute(
        "INSERT INTO inventory_balances (company_id,product_id,branch_id,quantity_on_hand) VALUES (?,?,?,?)",
        (company_id, product_id, branch_id, stock),
    )
    rconn.commit()
    rconn.close()

    client = app.test_client()
    client.post("/api/auth/login", json={"email": email, "password": password})
    client.get("/api/sub/retail/settings/tax")

    # sales.sale_number carries a bare (not company-scoped) UNIQUE constraint,
    # but _next_ref()'s counter starts at 1 per company -- every test's fresh
    # company would otherwise generate "SALE-000001" as its first sale and
    # collide with every other test's first sale in this shared temp DB.
    # Identical fix to retail_returns_wave0_test.py's own helper.
    import random as _random
    dconn = get_retail_conn()
    dconn.execute(
        "INSERT OR REPLACE INTO doc_sequences (company_id,doc_type,last_no) VALUES (?,'sale',?)",
        (company_id, _random.randint(1, 5_000_000)),
    )
    dconn.commit()
    dconn.close()

    return client, company_id, product_id, branch_id


def _add_second_product(cid, bid, price=50.0, tax_rate=0.0, stock=10, sku='RTS-2'):
    rconn = get_retail_conn()
    rconn.execute(
        "INSERT INTO products (id,company_id,sku,name,cost_price,sell_price,tax_rate) VALUES (?,?,?,?,5,?,?)",
        (str(uuid.uuid4()), cid, sku, f'Return Sync Item {sku}', price, tax_rate),
    )
    product_id = rconn.execute(
        "SELECT id FROM products WHERE company_id=? AND sku=?", (cid, sku)
    ).fetchone()[0]
    rconn.execute(
        "INSERT INTO inventory_balances (company_id,product_id,branch_id,quantity_on_hand) VALUES (?,?,?,?)",
        (cid, product_id, bid, stock),
    )
    rconn.commit()
    rconn.close()
    return product_id


def _make_credit_customer(cid, name='Credit Customer'):
    """Identical technique to retail_returns_wave0_test.py's own helper."""
    rconn = get_retail_conn()
    customer_id = str(uuid.uuid4())
    rconn.execute(
        "INSERT INTO customers (id,company_id,name,credit_mode,credit_limit,credit_balance) "
        "VALUES (?,?,?,'unlimited',0,0)",
        (customer_id, cid, name),
    )
    rconn.commit()
    rconn.close()
    return customer_id


def _return(client, sale_id, pid, quantity, idem=None, reason='test'):
    return client.post('/api/sub/retail/returns', json={
        'sale_id': sale_id,
        'items': [{'product_id': pid, 'quantity': quantity}],
        'reason': reason,
        'idempotency_key': idem or str(uuid.uuid4()),
    })


def _sale_uid(sale_id):
    conn = get_retail_conn()
    row = conn.execute("SELECT uid FROM sales WHERE id=?", (sale_id,)).fetchone()
    conn.close()
    return row['uid']


def _branch_uid(bid):
    conn = get_retail_conn()
    row = conn.execute("SELECT uid FROM branches WHERE id=?", (bid,)).fetchone()
    conn.close()
    return row['uid']


def _apply_synced_return(cid, sale_id, branch_id, payload_overrides):
    """Drives the REAL `SyncService._apply_event` -- the exact function this
    task's fix is inside -- against THIS device's own real retail.db (the
    same one create_return's HTTP route writes to), simulating "this return
    arrived via sync from another device". Same pattern
    retail_stock_sync_apply_hardening_test.py already established for this
    module: no bare second-device fixture is needed here because the point
    under test (see module docstring) is the interaction between this one
    INSERT and create_return's OWN `_prior` query, which only makes sense
    read against the SAME database.

    `client_factory=lambda: None` -- this call never pushes or pulls, only
    ever calls `_apply_event` directly, matching the hardening suite's own
    `service_b` fixture."""
    service = SyncService(client_factory=lambda: None, get_conn=get_retail_conn,
                           local_company_id_provider=lambda: cid)
    return_uid = payload_overrides.get('uid') or str(uuid.uuid4())
    payload = {
        'uid': return_uid, 'sale_uid': _sale_uid(sale_id), 'return_number': f'RET-SYNCED-{return_uid[:8]}',
        'branch_id': branch_id, 'branch_uid': _branch_uid(branch_id),
        'cashier': 'POS', 'reason': 'synced from device A',
        'refund_method': 'cash', 'refund_amount': 50.0,
        'status': 'completed', 'created_at': '2026-09-17 00:00:00',
        'actor_user_uid': None, 'terminal_id': None, 'created_at_utc': '2026-09-17T00:00:00Z',
    }
    payload.update(payload_overrides)
    ev = {'entity_type': 'return', 'event_type': 'create', 'payload': payload}
    conn = get_retail_conn()
    try:
        service._apply_event(conn, ev, local_company_id=cid)
        conn.commit()
    finally:
        conn.close()
    return return_uid


def _returns_row(return_uid):
    conn = get_retail_conn()
    row = conn.execute(
        "SELECT tender_refund_amount, ar_forgiven_amount, store_credit_amount, session_id, refund_amount "
        "FROM returns WHERE uid=?", (return_uid,)
    ).fetchone()
    conn.close()
    return row


# ── 1. Payload WITH the three split keys applies all three exactly ─────────

def test_synced_return_with_split_keys_applies_all_three_amounts_exactly():
    client, cid, pid, bid = _make_admin_and_product(price=50.0, tax_rate=0.0, stock=10)
    customer_id = _make_credit_customer(cid)
    sale = client.post('/api/sub/retail/sales', json={
        'items': [{'product_id': pid, 'quantity': 2}],
        'customer_id': customer_id, 'amount_paid': 40.0, 'payment_method': 'cash',
        'idempotency_key': str(uuid.uuid4()),
    }).get_json()['data']
    assert sale['total'] == 100.0
    assert sale['balance_due'] == 60.0

    return_uid = _apply_synced_return(cid, sale['id'], bid, {
        'refund_amount': 100.0,
        'tender_refund_amount': 40.0, 'ar_forgiven_amount': 60.0, 'store_credit_amount': 0.0,
    })

    row = _returns_row(return_uid)
    assert row is not None, "the synced return event was not applied at all"
    assert row['tender_refund_amount'] == 40.0
    assert row['ar_forgiven_amount'] == 60.0
    assert row['store_credit_amount'] == 0.0
    # Verified per this task's own instruction, not merely asserted: this
    # INSERT writes session_id as a literal SQL NULL a few lines below the
    # fix's own comment in sync_service.py, so a synced return can never be
    # session-scoped into ANY device's own X/Z report -- `_cash_session_
    # report` (retail_api.py) filters `WHERE ... session_id=?`, and SQL NULL
    # never equals a real session id.
    assert row['session_id'] is None


# ── 2. Payload WITHOUT the keys (older device) -- whole refund as tender ───

def test_synced_return_without_split_keys_falls_back_to_full_refund_as_tender():
    client, cid, pid, bid = _make_admin_and_product(price=50.0, tax_rate=0.0, stock=10)
    customer_id = _make_credit_customer(cid)
    sale = client.post('/api/sub/retail/sales', json={
        'items': [{'product_id': pid, 'quantity': 2}],
        'customer_id': customer_id, 'amount_paid': 40.0, 'payment_method': 'cash',
        'idempotency_key': str(uuid.uuid4()),
    }).get_json()['data']

    # An OLDER device's wire payload -- no split keys at all.
    return_uid = _apply_synced_return(cid, sale['id'], bid, {'refund_amount': 100.0})

    row = _returns_row(return_uid)
    assert row is not None
    assert row['tender_refund_amount'] == 100.0, (
        "an older device's payload (no split keys) must fall back to treating "
        f"the WHOLE refund as tender -- refusing a later over-refund is the "
        f"correct failure direction, not silently permitting a double payout "
        f"via a fabricated 0; got {dict(row)}"
    )
    assert row['ar_forgiven_amount'] == 0.0
    assert row['store_credit_amount'] == 0.0


def test_synced_return_with_explicit_null_split_keys_falls_back_same_as_absent():
    """`dict.get(key, default)` returns `None` -- not `default` -- when
    `key` IS present with value `None`. A payload that explicitly carries
    `tender_refund_amount: null` (a real possibility on a JSON wire payload)
    must take the identical conservative fallback as an absent key, never
    silently write a bare NULL into the money column."""
    client, cid, pid, bid = _make_admin_and_product(price=50.0, tax_rate=0.0, stock=10)
    customer_id = _make_credit_customer(cid)
    sale = client.post('/api/sub/retail/sales', json={
        'items': [{'product_id': pid, 'quantity': 2}],
        'customer_id': customer_id, 'amount_paid': 40.0, 'payment_method': 'cash',
        'idempotency_key': str(uuid.uuid4()),
    }).get_json()['data']

    return_uid = _apply_synced_return(cid, sale['id'], bid, {
        'refund_amount': 100.0,
        'tender_refund_amount': None, 'ar_forgiven_amount': None, 'store_credit_amount': None,
    })

    row = _returns_row(return_uid)
    assert row is not None
    assert row['tender_refund_amount'] == 100.0, (
        f"an explicit JSON null must take the same fallback as an absent key, got {dict(row)}"
    )
    assert row['ar_forgiven_amount'] == 0.0
    assert row['store_credit_amount'] == 0.0


# ── 3. THE decisive test -- the end-to-end double-payout consequence ───────

def test_second_return_after_sync_cannot_double_pay_tender():
    """This task's own measured reproduction, reproduced here: sale
    2x50=100, 40 cash / 60 AR. Return 1 (device A) claims 40 tender + 10 AR
    against product 1 (refund 50 -- tender_available was the full 40, since
    nothing had claimed it yet; remainder 10 is forgiven as AR). It arrives
    on this (receiving) device via the REAL `_apply_event` apply path -- see
    `_apply_synced_return`. A second, REAL return via the ACTUAL
    `/api/sub/retail/returns` route -- unmodified, never reached into -- for
    product 2 (also refund 50) must then be capped by create_return's OWN,
    untouched `_prior` query at whatever tender is ACTUALLY left: 40 - 40 = 0.

    `RETURN1_TENDER_ACTUALLY_PAID` is asserted as a plain constant, not read
    back from this device's own `returns` row for return 1: that row is the
    thing the defect corrupts (it stores 0 under the unfixed INSERT even
    though device A genuinely paid out 40), so reading it back here would
    silently hide the very double payout this test exists to catch. Real
    money left a real drawer at device A the moment that return happened;
    this test's job is to prove the till one physical shop uses can never
    ALSO pay out more than what remains, whatever its own copy of return 1
    ended up recording.

    Without this task's fix, the applied Return 1 row's tender_refund_amount
    lands at the column DEFAULT of 0 (dropped from the INSERT), so
    create_return's `_prior` sees no prior claim for the second return and it
    wrongly draws ANOTHER 40 of tender -- 80 paid out against 40 ever
    collected, the exact defect this task's PROOF REQUIRED section
    measures."""
    RETURN1_TENDER_ACTUALLY_PAID = 40.0
    RETURN1_AR_ACTUALLY_FORGIVEN = 10.0
    SALE_TENDER_EVER_COLLECTED = 40.0

    client, cid, pid, bid = _make_admin_and_product(price=50.0, tax_rate=0.0, stock=10)
    pid2 = _add_second_product(cid, bid, price=50.0, tax_rate=0.0, stock=10)
    customer_id = _make_credit_customer(cid)

    sale = client.post('/api/sub/retail/sales', json={
        'items': [{'product_id': pid, 'quantity': 1}, {'product_id': pid2, 'quantity': 1}],
        'customer_id': customer_id, 'amount_paid': SALE_TENDER_EVER_COLLECTED, 'payment_method': 'cash',
        'idempotency_key': str(uuid.uuid4()),
    }).get_json()['data']
    assert sale['total'] == 100.0
    assert sale['balance_due'] == 60.0

    # Return 1 -- as device A would actually have computed and paid out:
    # refund 50, tender_available 40 (nothing claimed yet) ->
    # tender_refund=min(50,40)=40, remainder 10 -> ar_forgiven=min(10,60,60)=10.
    _apply_synced_return(cid, sale['id'], bid, {
        'refund_amount': 50.0,
        'tender_refund_amount': RETURN1_TENDER_ACTUALLY_PAID,
        'ar_forgiven_amount': RETURN1_AR_ACTUALLY_FORGIVEN,
        'store_credit_amount': 0.0,
    })

    # Return 2 -- the REAL, unmodified create_return route, for the OTHER
    # product line, exactly like retail_returns_wave0_test.py's own
    # test_second_partial_return_only_draws_remaining_tender_pool.
    r2 = _return(client, sale['id'], pid2, quantity=1, reason='second till, after sync')
    assert r2.status_code == 200, r2.get_json()
    d2 = r2.get_json()['data']

    total_tender_paid = RETURN1_TENDER_ACTUALLY_PAID + d2['tender_refund_amount']
    assert d2['tender_refund_amount'] == 0.0, (
        f"the tender pool was already drained by the synced Return 1 (40 of the "
        f"40 ever collected) -- Return 2 must draw 0, not {d2['tender_refund_amount']} "
        f"(total tender paid out across both returns would be {total_tender_paid} "
        f"against only {SALE_TENDER_EVER_COLLECTED} ever collected)"
    )
    assert total_tender_paid == SALE_TENDER_EVER_COLLECTED, (
        f"total tender paid out across both returns must equal the "
        f"{SALE_TENDER_EVER_COLLECTED} actually collected, got {total_tender_paid}"
    )
    assert d2['ar_forgiven_amount'] == 50.0, (
        f"the remaining $50 of this return's value must be forgiven as AR "
        f"(balance_due_remaining 60 - 10 already forgiven = 50), got {d2['ar_forgiven_amount']}"
    )
