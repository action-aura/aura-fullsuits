"""ADVERSARIAL ACCEPTANCE PASS -- Phase 5 wave B1 (stock-moving sync).

THE DECISIVE TEST named by the acceptance brief: three real devices, in
three real OS processes, ALL TRADING (not one writer and two receivers),
across TWO operator-created branches, each device selling, adjusting stock,
receiving a purchase-order delivery, AND taking a return -- syncing
repeatedly in both directions -- and then:

  1. `compute_drift` (the REAL Phase 3 gate function, imported unchanged from
     `core.retail.stock_reconciliation`, never reimplemented) returns ZERO on
     EVERY device.
  2. Per-(product, branch) balances match an expected value computed
     independently of any device's own report -- not just the TOTAL, because
     a movement landing on the WRONG branch keeps total drift at zero while
     putting stock in the wrong physical place (exactly the shape that kept
     the duplicate-branch defect invisible before this phase).

Not a modification of any existing test file -- a new, standalone file. Not
built on `_stock_sync_harness.py`'s own `run_device()` (which only knows the
delivered `_stock_sync_device.py`'s narrower op vocabulary): uses
`_accept_harness.py`'s `run_accept_device()` against `_accept_device.py`
instead, which adds `create_po` branch routing this scenario needs.

Run:
    C:/Users/MSI/Desktop/aura-fullsuits/.venv/Scripts/python.exe -m pytest \
        products/retail/tests/_accept_convergence_test.py -v -s
"""
from __future__ import annotations

import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from _accept_harness import run_accept_device  # noqa: E402

PY = sys.executable


def test_three_real_devices_all_trading_across_two_branches_converge_to_zero_drift_and_correct_per_branch_balances(tmp_path):
    device_a = tmp_path / "device_a"
    device_b = tmp_path / "device_b"
    device_c = tmp_path / "device_c"
    relay_db = tmp_path / "relay.db"

    # ── 0. A originates the shared catalogue: one product, no opening stock
    #      yet (stock is filed against operator branches below, never a
    #      self-healed one -- self-heals deliberately queue no branch event,
    #      see sync_service.py's module docstring). ──────────────────────
    [shop] = run_accept_device(PY, device_a, relay_db, [
        {"op": "create_shop", "price": 20.0, "initial_stock": 0},
    ])
    cid_a, pid = shop["company_id"], shop["product_id"]

    _, branch_w = run_accept_device(PY, device_a, relay_db, [
        {"op": "login", "email": shop["email"], "password": shop["password"]},
        {"op": "create_branch", "name": "Warehouse", "address": "1 Industrial Way", "phone": "555-0001"},
    ])
    assert branch_w["status_code"] == 200, branch_w["body"]
    a_w, w_uid = branch_w["branch_id"], branch_w["branch_uid"]

    run_accept_device(PY, device_a, relay_db, [
        {"op": "login", "email": shop["email"], "password": shop["password"]},
        {"op": "adjust_stock", "product_id": pid, "quantity": 1000, "reason": "Opening stock, Warehouse",
         "branch_id": a_w},
        {"op": "push"},
    ])

    # ── B bootstraps, pulls the catalogue + Warehouse, then creates its OWN
    #    operator branch (Downtown) and pushes it. ─────────────────────────
    [bootstrap_b] = run_accept_device(PY, device_b, relay_db, [{"op": "bootstrap"}])
    cid_b = bootstrap_b["company_id"]
    run_accept_device(PY, device_b, relay_db, [{"op": "pull"}])

    [lookup_w_b] = run_accept_device(PY, device_b, relay_db, [{"op": "branch_by_uid", "uid": w_uid}])
    b_w = lookup_w_b["branch"]["id"]

    _, branch_d = run_accept_device(PY, device_b, relay_db, [
        {"op": "login", "email": bootstrap_b["email"], "password": bootstrap_b["password"]},
        {"op": "create_branch", "name": "Downtown", "address": "2 Market Sq", "phone": "555-0002"},
    ])
    assert branch_d["status_code"] == 200, branch_d["body"]
    b_d, d_uid = branch_d["branch_id"], branch_d["branch_uid"]
    run_accept_device(PY, device_b, relay_db, [{"op": "push"}])

    # ── C bootstraps AFTER B has already pushed Downtown, so one pull gets
    #    both branches. ─────────────────────────────────────────────────────
    [bootstrap_c] = run_accept_device(PY, device_c, relay_db, [{"op": "bootstrap"}])
    cid_c = bootstrap_c["company_id"]
    run_accept_device(PY, device_c, relay_db, [{"op": "pull"}])

    [lookup_w_c] = run_accept_device(PY, device_c, relay_db, [{"op": "branch_by_uid", "uid": w_uid}])
    c_w = lookup_w_c["branch"]["id"]
    [lookup_d_c] = run_accept_device(PY, device_c, relay_db, [{"op": "branch_by_uid", "uid": d_uid}])
    c_d = lookup_d_c["branch"]["id"]

    # A pulls back to learn about Downtown too.
    run_accept_device(PY, device_a, relay_db, [{"op": "pull"}])
    [lookup_d_a] = run_accept_device(PY, device_a, relay_db, [{"op": "branch_by_uid", "uid": d_uid}])
    a_d = lookup_d_a["branch"]["id"]

    # All three devices now resolve BOTH operator branches to their own local
    # ids: (a_w, a_d), (b_w, b_d), (c_w, c_d).

    # A funds Downtown with its own opening stock too (a real till cannot
    # sell against a branch that has never received anything) and pushes --
    # B and C both pull it BEFORE their own trading rounds try to sell
    # against Downtown below.
    run_accept_device(PY, device_a, relay_db, [
        {"op": "login", "email": shop["email"], "password": shop["password"]},
        {"op": "adjust_stock", "product_id": pid, "quantity": 400, "reason": "Opening stock, Downtown",
         "branch_id": a_d},
        {"op": "push"},
    ])
    run_accept_device(PY, device_b, relay_db, [{"op": "pull"}])
    run_accept_device(PY, device_c, relay_db, [{"op": "pull"}])

    # ── Trading round: EVERY device sells, adjusts, receives a delivery
    #    (supplier + PO + receive), and takes a return -- across BOTH
    #    branches. Nothing pushed yet; this is all local, concurrent
    #    business happening on three real tills before anyone syncs again. ──

    # Device A: sale + return at Warehouse, adjustment at Downtown, delivery
    #           received at Warehouse.
    _, a_sup, a_sell = run_accept_device(PY, device_a, relay_db, [
        {"op": "login", "email": shop["email"], "password": shop["password"]},
        {"op": "create_supplier", "name": "A Supplier"},
        {"op": "sell", "product_id": pid, "quantity": 30, "branch_id": a_w},
    ])
    assert a_sup["status_code"] == 200, a_sup["body"]
    assert a_sell["status_code"] == 200, a_sell["body"]
    a_supplier_id, a_sale_id = a_sup["supplier_id"], a_sell["sale_id"]

    _, a_po, a_adjust = run_accept_device(PY, device_a, relay_db, [
        {"op": "login", "email": shop["email"], "password": shop["password"]},
        {"op": "create_po", "supplier_id": a_supplier_id,
         "items": [{"product_id": pid, "quantity": 200, "unit_cost": 8.0}], "branch_id": a_w},
        {"op": "adjust_stock", "product_id": pid, "quantity": 15, "reason": "Found extra units at Downtown",
         "branch_id": a_d},
    ])
    assert a_po["status_code"] == 200, a_po["body"]
    assert a_adjust["status_code"] == 200, a_adjust["body"]

    _, a_receive, a_return = run_accept_device(PY, device_a, relay_db, [
        {"op": "login", "email": shop["email"], "password": shop["password"]},
        {"op": "receive_po", "po_id": a_po["po_id"]},
        {"op": "create_return", "sale_id": a_sale_id, "items": [{"product_id": pid, "quantity": 5}]},
    ])
    assert a_receive["status_code"] == 200, a_receive["body"]
    assert a_return["status_code"] == 200, a_return["body"]
    run_accept_device(PY, device_a, relay_db, [{"op": "push"}])

    # Device B: sale + return at Downtown, adjustment (shrinkage) at
    #           Warehouse, delivery received at Downtown.
    _, b_sup, b_sell = run_accept_device(PY, device_b, relay_db, [
        {"op": "login", "email": bootstrap_b["email"], "password": bootstrap_b["password"]},
        {"op": "create_supplier", "name": "B Supplier"},
        {"op": "sell", "product_id": pid, "quantity": 12, "branch_id": b_d},
    ])
    assert b_sup["status_code"] == 200, b_sup["body"]
    assert b_sell["status_code"] == 200, b_sell["body"]
    b_supplier_id, b_sale_id = b_sup["supplier_id"], b_sell["sale_id"]

    _, b_po, b_adjust = run_accept_device(PY, device_b, relay_db, [
        {"op": "login", "email": bootstrap_b["email"], "password": bootstrap_b["password"]},
        {"op": "create_po", "supplier_id": b_supplier_id,
         "items": [{"product_id": pid, "quantity": 90, "unit_cost": 8.0}], "branch_id": b_d},
        {"op": "adjust_stock", "product_id": pid, "quantity": -4, "reason": "Shrinkage found at Warehouse",
         "branch_id": b_w},
    ])
    assert b_po["status_code"] == 200, b_po["body"]
    assert b_adjust["status_code"] == 200, b_adjust["body"]

    _, b_receive, b_return = run_accept_device(PY, device_b, relay_db, [
        {"op": "login", "email": bootstrap_b["email"], "password": bootstrap_b["password"]},
        {"op": "receive_po", "po_id": b_po["po_id"]},
        {"op": "create_return", "sale_id": b_sale_id, "items": [{"product_id": pid, "quantity": 3}]},
    ])
    assert b_receive["status_code"] == 200, b_receive["body"]
    assert b_return["status_code"] == 200, b_return["body"]
    run_accept_device(PY, device_b, relay_db, [{"op": "push"}])

    # Device C: TWO sales (one per branch), an adjustment at Warehouse, a
    #           delivery received at Downtown, and a return at Downtown.
    _, c_sup, c_sell_w, c_sell_d = run_accept_device(PY, device_c, relay_db, [
        {"op": "login", "email": bootstrap_c["email"], "password": bootstrap_c["password"]},
        {"op": "create_supplier", "name": "C Supplier"},
        {"op": "sell", "product_id": pid, "quantity": 9, "branch_id": c_w},
        {"op": "sell", "product_id": pid, "quantity": 6, "branch_id": c_d},
    ])
    assert c_sup["status_code"] == 200, c_sup["body"]
    assert c_sell_w["status_code"] == 200, c_sell_w["body"]
    assert c_sell_d["status_code"] == 200, c_sell_d["body"]
    c_supplier_id, c_sale_d_id = c_sup["supplier_id"], c_sell_d["sale_id"]

    _, c_po, c_adjust = run_accept_device(PY, device_c, relay_db, [
        {"op": "login", "email": bootstrap_c["email"], "password": bootstrap_c["password"]},
        {"op": "create_po", "supplier_id": c_supplier_id,
         "items": [{"product_id": pid, "quantity": 70, "unit_cost": 8.0}], "branch_id": c_d},
        {"op": "adjust_stock", "product_id": pid, "quantity": 25, "reason": "Stocktake found extra at Warehouse",
         "branch_id": c_w},
    ])
    assert c_po["status_code"] == 200, c_po["body"]
    assert c_adjust["status_code"] == 200, c_adjust["body"]

    _, c_receive, c_return = run_accept_device(PY, device_c, relay_db, [
        {"op": "login", "email": bootstrap_c["email"], "password": bootstrap_c["password"]},
        {"op": "receive_po", "po_id": c_po["po_id"]},
        {"op": "create_return", "sale_id": c_sale_d_id, "items": [{"product_id": pid, "quantity": 2}]},
    ])
    assert c_receive["status_code"] == 200, c_receive["body"]
    assert c_return["status_code"] == 200, c_return["body"]
    run_accept_device(PY, device_c, relay_db, [{"op": "push"}])

    # ── Everyone pulls once, in one batch each -- genuinely INTERLEAVED
    #    events from TWO other real, independently-writing devices land in a
    #    single pull, not a clean one-writer-at-a-time handoff. ────────────
    run_accept_device(PY, device_a, relay_db, [{"op": "pull"}])
    run_accept_device(PY, device_b, relay_db, [{"op": "pull"}])
    run_accept_device(PY, device_c, relay_db, [{"op": "pull"}])

    # Expected balances, computed independently of any device's own report:
    #
    #   Warehouse = 1000 (A opening) - 30 (A sale) + 200 (A PO) + 5 (A return)
    #               - 4 (B shrinkage) - 9 (C sale) + 25 (C stocktake)
    #             = 1187
    #   Downtown  = 400 (A opening, funded after Downtown itself synced) +
    #               15 (A adjustment) - 12 (B sale) + 90 (B PO) + 3 (B return)
    #               - 6 (C sale) + 70 (C PO) + 2 (C return)
    #             = 562
    #
    # Deliberately DIFFERENT totals at the two branches: a movement landing
    # on the WRONG branch would be very likely to show up as a wrong number
    # at ONE of the two, even though total stock across both branches (1749)
    # would still balance -- see this file's own module docstring.
    expected_w = 1000 - 30 + 200 + 5 - 4 - 9 + 25
    expected_d = 400 + 15 - 12 + 90 + 3 - 6 + 70 + 2
    assert expected_w == 1187
    assert expected_d == 562

    # A's own `create_shop` self-heals a private "Main Branch" (never synced
    # -- see `_default_branch`'s "Wave B, CORRECTED" comment) the moment it
    # creates its first product, regardless of `initial_stock`; B and C
    # never create a product locally (`bootstrap` deliberately calls
    # `_default_branch` for nothing -- see `_stock_sync_device.py`'s own
    # `bootstrap` op docstring), so their only branch rows are the two
    # OPERATOR-created ones that actually crossed the wire.
    expected_branch_counts = {"A": 3, "B": 2, "C": 2}

    for label, run_dev, cid, bw, bd in (
        ("A", device_a, cid_a, a_w, a_d),
        ("B", device_b, cid_b, b_w, b_d),
        ("C", device_c, cid_c, c_w, c_d),
    ):
        [bal_w] = run_accept_device(PY, run_dev, relay_db, [
            {"op": "get_balance", "company_id": cid, "product_id": pid, "branch_id": bw},
        ])
        assert bal_w["quantity_on_hand"] == expected_w, (
            f"device {label}'s Warehouse balance is wrong: {bal_w} (expected {expected_w})"
        )
        [bal_d] = run_accept_device(PY, run_dev, relay_db, [
            {"op": "get_balance", "company_id": cid, "product_id": pid, "branch_id": bd},
        ])
        assert bal_d["quantity_on_hand"] == expected_d, (
            f"device {label}'s Downtown balance is wrong: {bal_d} (expected {expected_d})"
        )

        # THE decisive assertion: the REAL Phase 3 gate function, zero on
        # EVERY device. Printed (not just asserted) so a `-s` run leaves the
        # actual returned value in the transcript as acceptance evidence.
        [drift] = run_accept_device(PY, run_dev, relay_db, [{"op": "compute_drift", "company_id": cid}])
        print(f"ACCEPTANCE compute_drift(device={label}, company_id={cid!r}) -> {drift['drift']!r}")
        assert drift["drift"] == [], f"device {label}'s ledger disagrees with its own cache: {drift['drift']}"

        # And no PHANTOM or DUPLICATED branch row snuck in anywhere along the
        # way -- the exact regression shape this whole phase exists to close
        # (two permanently-unmerged "Main Branch" rows). Expected count is
        # per-device (see `expected_branch_counts` above), not a blanket 2.
        [branches] = run_accept_device(PY, run_dev, relay_db, [
            {"op": "list_branches", "company_id": cid},
        ])
        expected_count = expected_branch_counts[label]
        assert len(branches["branches"]) == expected_count, (
            f"device {label} ended up with {len(branches['branches'])} branch rows, "
            f"expected {expected_count}: {branches['branches']}"
        )
