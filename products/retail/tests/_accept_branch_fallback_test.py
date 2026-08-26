"""ADVERSARIAL ACCEPTANCE PASS -- Phase 5 wave B1 (stock-moving sync).

Requirement D, "the branch fallback": `_resolve_branch_id` falls back to the
receiver's own default branch when a pulled row's `branch_uid` does not
resolve locally. This pass proves BOTH halves the acceptance brief names
explicitly, in ONE end-to-end scenario, across real OS processes:

  1. The fallback DOES fire when an operator-created branch has not yet
     synced -- and when it fires, the outcome is VISIBLE: a real WARNING is
     logged, not silently swallowed. Proved by reading the ACTUAL log output
     (see `_accept_device.py`'s own logging-capture addition), not by
     inferring it from a balance number the way every other test in this
     suite necessarily does.
  2. The fallback STOPS firing -- zero warnings -- once that SAME branch has
     legitimately synced, for a LATER movement naming the identical
     `branch_uid`. The delivered suite already proves the balance ends up
     correct once a branch has synced
     (`test_branch_uid_resolution_stops_falling_back_once_the_branch_has_
     synced`); this test additionally proves the ABSENCE of the warning that
     would mean the fallback fired anyway and got lucky.

Run:
    C:/Users/MSI/Desktop/aura-fullsuits/.venv/Scripts/python.exe -m pytest \
        products/retail/tests/_accept_branch_fallback_test.py -v -s
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from _accept_harness import run_accept_device  # noqa: E402

PY = sys.executable


def test_branch_fallback_is_logged_when_it_fires_and_silent_never_replaces_it(tmp_path):
    device_a = tmp_path / "device_a"
    device_b = tmp_path / "device_b"
    relay_db = tmp_path / "relay.db"

    [shop] = run_accept_device(PY, device_a, relay_db, [
        {"op": "create_shop", "price": 14.0, "initial_stock": 0},
    ])
    pid = shop["product_id"]

    _, branch = run_accept_device(PY, device_a, relay_db, [
        {"op": "login", "email": shop["email"], "password": shop["password"]},
        {"op": "create_branch", "name": "Ops Branch", "address": "9 Ops Ave", "phone": "555-0009"},
    ])
    assert branch["status_code"] == 200, branch["body"]
    o_bid, o_buid = branch["branch_id"], branch["branch_uid"]

    _, first_adjust = run_accept_device(PY, device_a, relay_db, [
        {"op": "login", "email": shop["email"], "password": shop["password"]},
        {"op": "adjust_stock", "product_id": pid, "quantity": 500, "reason": "Opening stock, Ops Branch",
         "branch_id": o_bid},
    ])
    assert first_adjust["status_code"] == 200, first_adjust["body"]

    # Capture the REAL branch/create payload verbatim, from A's own outbox,
    # BEFORE stripping it out below -- used to reinject it later, exactly
    # matching what A actually queued (never a hand-typed guess).
    [outbox] = run_accept_device(PY, device_a, relay_db, [
        {"op": "raw_select",
         "sql": "SELECT entity_type, entity_id, event_type, payload FROM sync_outbox WHERE entity_type='branch'"},
    ])
    assert len(outbox["rows"]) == 1
    branch_event_row = outbox["rows"][0]

    # Strip the branch event out BEFORE pushing -- B's first batch carries
    # the product and the movement (which names `o_buid`) but NOT the branch
    # itself, forcing `_resolve_branch_id`'s tier-2 fallback to fire on a
    # real pull through the real routes, not a hand-crafted scenario.
    run_accept_device(PY, device_a, relay_db, [
        {"op": "outbox_delete_entity_type", "entity_type": "branch"},
        {"op": "push"},
    ])

    [bootstrap] = run_accept_device(PY, device_b, relay_db, [{"op": "bootstrap"}])
    cid_b = bootstrap["company_id"]

    [pull_1] = run_accept_device(PY, device_b, relay_db, [{"op": "pull"}])

    # ── Half 1: the fallback FIRED, and it is VISIBLE. ─────────────────────
    warnings_1 = pull_1.get("_log_warnings") or []
    assert warnings_1 != [], (
        "the branch fallback fired (B had never seen Ops Branch) but logged "
        "NOTHING -- a wrong-branch filing must never be silent (DEFECT 3)"
    )
    joined = " ".join(warnings_1)
    assert "did not resolve" in joined, warnings_1
    assert o_buid in joined, f"the discarded branch_uid itself must appear in the warning: {warnings_1}"

    [branches_after_1] = run_accept_device(PY, device_b, relay_db, [
        {"op": "list_branches", "company_id": cid_b},
    ])
    assert len(branches_after_1["branches"]) == 1, (
        "B should have self-healed exactly one (private) default branch to "
        f"absorb the fallback: {branches_after_1['branches']}"
    )
    b_default_bid = branches_after_1["branches"][0]["id"]

    [bal_default] = run_accept_device(PY, device_b, relay_db, [
        {"op": "get_balance", "company_id": cid_b, "product_id": pid, "branch_id": b_default_bid},
    ])
    assert bal_default["quantity_on_hand"] == 500.0, (
        "the fallback-filed movement did not land on B's self-healed default branch"
    )

    # ── The branch legitimately arrives (an operator replaying it from
    #    Owner's console, or simply a later ordinary sync tick). ───────────
    conn = sqlite3.connect(str(relay_db))
    try:
        conn.execute(
            "INSERT INTO relay_events (payload) VALUES (?)",
            (json.dumps({
                "entity_type": branch_event_row["entity_type"], "entity_id": branch_event_row["entity_id"],
                "event_type": branch_event_row["event_type"], "payload": json.loads(branch_event_row["payload"]),
            }),),
        )
        conn.commit()
    finally:
        conn.close()

    [pull_2] = run_accept_device(PY, device_b, relay_db, [{"op": "pull"}])
    assert (pull_2.get("_log_warnings") or []) == [], (
        "a pure branch/create event (no movement in this batch) should never "
        "itself touch the fallback path"
    )

    [branches_after_2] = run_accept_device(PY, device_b, relay_db, [
        {"op": "list_branches", "company_id": cid_b},
    ])
    assert len(branches_after_2["branches"]) == 2, (
        f"Ops Branch did not arrive as a SECOND, distinct row: {branches_after_2['branches']}"
    )
    [lookup] = run_accept_device(PY, device_b, relay_db, [{"op": "branch_by_uid", "uid": o_buid}])
    assert lookup["branch"] is not None
    b_ops_bid = lookup["branch"]["id"]
    assert b_ops_bid != b_default_bid

    # ── Half 2: a LATER movement naming the SAME branch_uid -- now that it
    #    has synced -- resolves via tier 1, and the fallback does NOT fire
    #    (zero warnings), not merely "ends up at the right number by luck". ─
    _, second_adjust, _ = run_accept_device(PY, device_a, relay_db, [
        {"op": "login", "email": shop["email"], "password": shop["password"]},
        {"op": "adjust_stock", "product_id": pid, "quantity": 50, "reason": "Second delivery, branch now synced",
         "branch_id": o_bid},
        {"op": "push"},
    ])
    assert second_adjust["status_code"] == 200, second_adjust["body"]

    [pull_3] = run_accept_device(PY, device_b, relay_db, [{"op": "pull"}])
    assert (pull_3.get("_log_warnings") or []) == [], (
        f"the fallback fired for a branch that HAD already synced: {pull_3.get('_log_warnings')}"
    )

    [bal_ops] = run_accept_device(PY, device_b, relay_db, [
        {"op": "get_balance", "company_id": cid_b, "product_id": pid, "branch_id": b_ops_bid},
    ])
    assert bal_ops["quantity_on_hand"] == 50.0, (
        "the second movement did not land on the CORRECT (now-synced) branch"
    )
    [bal_default_after] = run_accept_device(PY, device_b, relay_db, [
        {"op": "get_balance", "company_id": cid_b, "product_id": pid, "branch_id": b_default_bid},
    ])
    assert bal_default_after["quantity_on_hand"] == 500.0, (
        "the second movement leaked into the self-healed default branch instead"
    )

    [drift] = run_accept_device(PY, device_b, relay_db, [{"op": "compute_drift", "company_id": cid_b}])
    print(f"ACCEPTANCE compute_drift(branch-fallback test, device=B) -> {drift['drift']!r}")
    assert drift["drift"] == [], drift["drift"]
