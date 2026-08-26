"""Aura Retail -- Phase 5 wave B (stock-moving sync): a Phase 4 cash drawer
must survive a pull batch that carries BOTH wave-B1 entity types at once.

PROMOTED FROM SCRATCH (formerly `products/retail/tests/_verify_m6_drawer_
survives_sync.py`, an adversarial verifier's disposable mutation-proof
script -- deleted once its content landed here). MUTATION PROOF M6: with a
cash drawer OPEN on the RECEIVING device, apply `branch` AND
`inventory_movement` pull events together -- both wave-B1 entity types, not
just `branch` alone, which is all the delivered `retail_stock_sync_test.py`'s
own `test_a_new_branch_arriving_does_not_disturb_an_open_cash_drawer` ever
exercises -- and prove the drawer:

  1. is still owned by its own terminal (still reachable/closable through
     the real close route, not just "the row still exists"),
  2. its expected-cash math (opening_float + cash_sales - cash_refunds +
     movements) is UNCHANGED by the sync batch that landed in between, and
  3. it actually closes successfully with the correct locked totals
     (variance=0.0).

Two REAL devices, separate OS processes, through the real routes -- same
technique as retail_stock_sync_test.py's own section 3, and for the same
reason: the delivered suite's cash-drawer test (retail_cash_drawer_test.py)
uses the single-process `install_b` fixture (a raw sqlite connection, no
real Flask app), which cannot exercise the real POST .../close route at all.

Uses `_verify_wb1_device.py` (kept, not deleted, alongside `_stock_sync_
device.py` and `_stock_sync_harness.py`) as the device subprocess -- it
understands `cash_session_open`/`cash_session_close`/`raw_select`/
`list_branches`, none of which the delivered `_stock_sync_device.py`
exposes.

A's opening stock is filed against an OPERATOR-created branch
(`create_branch`), not the self-healed default `create_shop` also produces
-- `_default_branch`'s self-heal deliberately queues NO `branch` sync event
any more (retail_api.py, "Wave B, CORRECTED"), so without this the batch B
pulls would carry only an `inventory_movement`, not the two-entity-type
batch this proof is named for.

Run:
    pytest products/retail/tests/retail_stock_sync_cash_drawer_test.py -v
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

PY = sys.executable
_DEVICE_SCRIPT = Path(__file__).resolve().parent / "_verify_wb1_device.py"


def _run_device(app_data_dir: Path, relay_db_path: Path, actions: list, timeout: float = 60.0) -> list:
    """Same contract as _stock_sync_harness.run_device(), but against
    _verify_wb1_device.py (which understands cash_session_open/close/status
    and raw_select, none of which the delivered _stock_sync_device.py
    exposes)."""
    app_data_dir = Path(app_data_dir)
    app_data_dir.mkdir(parents=True, exist_ok=True)
    actions_path = app_data_dir / "_actions_in.json"
    output_path = app_data_dir / "_actions_out.json"
    actions_path.write_text(json.dumps(actions), encoding="utf-8")
    if output_path.exists():
        output_path.unlink()
    proc = subprocess.run(
        [PY, str(_DEVICE_SCRIPT), str(app_data_dir), str(relay_db_path), str(actions_path), str(output_path)],
        capture_output=True, text=True, timeout=timeout,
    )
    if proc.returncode != 0 or not output_path.exists():
        raise RuntimeError(
            f"device subprocess failed (exit={proc.returncode}) for app_data_dir={app_data_dir}\n"
            f"actions={actions!r}\n--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
        )
    return json.loads(output_path.read_text(encoding="utf-8"))


def test_an_open_cash_drawer_survives_a_pull_batch_carrying_branch_and_inventory_movement_together(tmp_path):
    device_a = tmp_path / "device_a"
    device_b = tmp_path / "device_b"
    relay_db = tmp_path / "relay.db"

    # Device A: an entirely separate company that originates a shop and
    # rings its own business -- its branch AND inventory_movement events
    # are what will land on B mid-drawer.
    [shop] = _run_device(device_a, relay_db, [
        {"op": "create_shop", "price": 40.0, "initial_stock": 0},
    ])
    # An OPERATOR-created branch -- see this file's own module docstring for
    # why `create_shop`'s self-healed default no longer produces a `branch`
    # event at all.
    _, branch = _run_device(device_a, relay_db, [
        {"op": "login", "email": shop["email"], "password": shop["password"]},
        {"op": "create_branch", "name": "A Sync Branch", "address": "", "phone": ""},
    ])
    assert branch["status_code"] == 200, branch["body"]
    a_branch_id = branch["branch_id"]

    _, adjust_result, sell_result, _ = _run_device(device_a, relay_db, [
        {"op": "login", "email": shop["email"], "password": shop["password"]},
        {"op": "adjust_stock", "product_id": shop["product_id"], "quantity": 1000,
         "reason": "Opening stock, operator branch", "branch_id": a_branch_id},
        {"op": "sell", "product_id": shop["product_id"], "quantity": 5, "branch_id": a_branch_id},
        {"op": "push"},
    ])
    assert adjust_result["status_code"] == 200, adjust_result["body"]
    assert sell_result["status_code"] == 200, sell_result["body"]

    # Device B: the receiver, with its own product and its own drawer OPEN.
    [b_shop] = _run_device(device_b, relay_db, [
        {"op": "create_shop", "price": 20.0, "initial_stock": 300, "product_name": "B Own Widget"},
    ])
    # `login` and everything that needs the session it establishes must be
    # in the SAME _run_device() call -- each call is a FRESH subprocess (a
    # real relaunch), so a Flask test_client's session cookie from one call
    # is gone by the next, exactly like retail_stock_sync_test.py's own
    # section-3 tests already account for.
    _, open_result, b_sale = _run_device(device_b, relay_db, [
        {"op": "login", "email": b_shop["email"], "password": b_shop["password"]},
        {"op": "cash_session_open", "branch_id": b_shop["branch_id"], "opening_float": 100.0},
        # B rings ONE real cash sale INSIDE this session, so expected_cash
        # has a non-trivial cash_sales component to prove unchanged, not
        # just a bare opening float.
        {"op": "sell", "product_id": b_shop["product_id"], "quantity": 3, "branch_id": b_shop["branch_id"]},
    ])
    assert open_result["status_code"] == 200, f"cash_session_open failed: {open_result}"
    session_id = open_result["session_id"]
    assert b_sale["status_code"] == 200, f"B's own sale failed: {b_sale}"

    # Snapshot the raw cash_sessions row AND an independent x-report-shaped
    # figure BEFORE the sync batch lands.
    before_row = _run_device(device_b, relay_db, [
        {"op": "raw_select", "sql": "SELECT * FROM cash_sessions WHERE id=?", "params": [session_id]},
    ])[0]["rows"][0]
    before_cash_sales = _run_device(device_b, relay_db, [
        {"op": "raw_select",
         "sql": "SELECT COALESCE(SUM(p.amount),0) AS total FROM payments p JOIN sales s ON p.sale_id=s.id "
                "WHERE s.session_id=? AND p.direction='in' AND p.method='cash' AND p.related_type='sale' "
                "AND COALESCE(p.status,'active')='active'",
         "params": [session_id]},
    ])[0]["rows"][0]["total"]

    # THE DECISIVE STEP: pull A's batch onto B. It carries BOTH wave-B1
    # entity types -- a `branch` create AND `inventory_movement` creates
    # (A's own opening stock + A's own sale) -- while B's drawer sits open.
    _run_device(device_b, relay_db, [{"op": "pull"}])

    after_row = _run_device(device_b, relay_db, [
        {"op": "raw_select", "sql": "SELECT * FROM cash_sessions WHERE id=?", "params": [session_id]},
    ])[0]["rows"][0]
    after_cash_sales = _run_device(device_b, relay_db, [
        {"op": "raw_select",
         "sql": "SELECT COALESCE(SUM(p.amount),0) AS total FROM payments p JOIN sales s ON p.sale_id=s.id "
                "WHERE s.session_id=? AND p.direction='in' AND p.method='cash' AND p.related_type='sale' "
                "AND COALESCE(p.status,'active')='active'",
         "params": [session_id]},
    ])[0]["rows"][0]["total"]

    assert before_row == after_row, (
        f"the open cash session row was disturbed by an arriving branch+inventory_movement batch: "
        f"{before_row} -> {after_row}"
    )
    assert before_cash_sales == after_cash_sales, (
        f"cash_sales total for this session changed after sync: {before_cash_sales} -> {after_cash_sales}"
    )
    assert after_row["status"] == "open", f"session was no longer open after sync: {after_row['status']!r}"

    # Prove B's OWN sale is unambiguously still owned by B's OWN terminal --
    # a synced sale from A must never be attributable into B's drawer.
    sale_count_in_session = _run_device(device_b, relay_db, [
        {"op": "raw_select", "sql": "SELECT COUNT(*) AS c FROM sales WHERE session_id=?", "params": [session_id]},
    ])[0]["rows"][0]["c"]
    assert sale_count_in_session == 1, (
        f"expected exactly B's own 1 sale in this session, found {sale_count_in_session}"
    )

    # STILL CLOSABLE: expected_cash = 100 opening + (3 * 20 = 60 cash sale) = 160.
    expected_cash = 100.0 + after_cash_sales
    # `login` again -- fresh subprocess, session cookie from the earlier call
    # is gone, same reasoning as the open/sell call above.
    _, close_result = _run_device(device_b, relay_db, [
        {"op": "login", "email": b_shop["email"], "password": b_shop["password"]},
        {"op": "cash_session_close", "session_id": session_id, "closing_float_counted": expected_cash},
    ])
    assert close_result["status_code"] == 200, f"drawer failed to close after sync: {close_result}"

    closed_row = _run_device(device_b, relay_db, [
        {"op": "raw_select", "sql": "SELECT * FROM cash_sessions WHERE id=?", "params": [session_id]},
    ])[0]["rows"][0]
    assert closed_row["status"] in ("ended", "closed"), f"unexpected post-close status: {closed_row['status']!r}"
    assert closed_row["closing_float_expected"] == expected_cash, (
        f"locked expected_cash is wrong: {closed_row['closing_float_expected']} != {expected_cash}"
    )
    assert closed_row["variance"] == 0.0, f"variance should be zero (exact count): {closed_row['variance']}"
    assert closed_row["terminal_id"] == before_row["terminal_id"], (
        f"terminal ownership changed across the close: {before_row['terminal_id']} -> {closed_row['terminal_id']}"
    )
