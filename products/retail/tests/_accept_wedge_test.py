"""ADVERSARIAL ACCEPTANCE PASS -- Phase 5 wave B1 (stock-moving sync).

Requirement C, "wedge resistance": wave A's worst defect was not a wrong
number, it was sync stopping PERMANENTLY (an IntegrityError escaping the
pull loop, the cursor never advancing, every event from every device stuck
behind it forever). Three separate proofs, each its own test function:

  1. `test_cursor_advances_and_sibling_events_land_when_the_parent_never_
      arrives_in_this_batch` -- a movement naming a product the receiver has
      never seen is quarantined, but the CURSOR STILL ADVANCES (read
      directly via `raw_select` against `sync_cursor`, not inferred) and the
      OTHER events in the same batch (the branch) still land. Closest to the
      delivered suite's own `test_a_product_that_never_arrives_quarantines_
      the_movement_across_real_processes`, but that test never actually
      reads the cursor value -- this one does.

  2. `test_a_batch_delivered_in_reversed_order_still_converges_in_one_pull`
      -- the SAME real events (captured verbatim from a real device's own
      outbox, not hand-typed guesses), delivered in DELIBERATELY REVERSED
      order (movement before its product, movement before its branch) in
      ONE relay batch. Proves the quarantine/retry mechanism inside
      `apply_pull_result` (events applied in order, THEN quarantined rows
      retried) converges the WHOLE batch in the SAME pull, not a second one
      -- and that this is the ORDINARY (not exceptional) case once the
      branch itself has synced: zero fallback-warning log lines.

  3. `test_applying_the_same_batch_three_times_is_byte_identical` -- MUTATION
      PROOF #2 (idempotency), generalised past the delivered suite's own
      in-process, twice-only, balance-and-count-only version
      (`test_replaying_the_full_pull_result_twice_does_not_double_stock`):
      here the SAME captured batch is applied THREE times, across a REAL
      process boundary (`apply_result_raw`, an acceptance-pass-only device
      op -- see `_accept_device.py`'s own module docstring), and the
      comparison is a FULL ordered snapshot of every row and every column in
      every affected table, not a sum.

Run:
    C:/Users/MSI/Desktop/aura-fullsuits/.venv/Scripts/python.exe -m pytest \
        products/retail/tests/_accept_wedge_test.py -v -s
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from _accept_harness import run_accept_device  # noqa: E402

PY = sys.executable


def _full_snapshot(run_dev, relay_db):
    """Every row, every column, of every table this phase's apply code can
    touch -- ordered deterministically so two snapshots are directly
    comparable as plain Python data. Deliberately `SELECT *` (never a
    hand-picked column list): a byte-identical proof that silently ignores a
    column would not be one."""
    tables = {
        "products": "SELECT * FROM products ORDER BY id",
        "branches": "SELECT * FROM branches ORDER BY id",
        "inventory_movements": "SELECT * FROM inventory_movements ORDER BY id",
        "inventory_balances": "SELECT * FROM inventory_balances ORDER BY id",
        "sync_apply_quarantine": "SELECT * FROM sync_apply_quarantine ORDER BY entity_id, event_type",
    }
    results = run_accept_device(PY, run_dev, relay_db, [
        {"op": "raw_select", "sql": sql} for sql in tables.values()
    ])
    return {name: res["rows"] for name, res in zip(tables.keys(), results)}


def test_cursor_advances_and_sibling_events_land_when_the_parent_never_arrives_in_this_batch(tmp_path):
    device_a = tmp_path / "device_a"
    device_b = tmp_path / "device_b"
    relay_db = tmp_path / "relay.db"

    [shop] = run_accept_device(PY, device_a, relay_db, [
        {"op": "create_shop", "price": 8.0, "initial_stock": 0},
    ])
    pid = shop["product_id"]

    _, branch = run_accept_device(PY, device_a, relay_db, [
        {"op": "login", "email": shop["email"], "password": shop["password"]},
        {"op": "create_branch", "name": "Wedge Test Branch", "address": "", "phone": ""},
    ])
    assert branch["status_code"] == 200, branch["body"]
    op_bid, op_buid = branch["branch_id"], branch["branch_uid"]

    run_accept_device(PY, device_a, relay_db, [
        {"op": "login", "email": shop["email"], "password": shop["password"]},
        {"op": "adjust_stock", "product_id": pid, "quantity": 60, "reason": "Opening stock, wedge test",
         "branch_id": op_bid},
        # The product event is stripped BEFORE push -- reproducing Owner's
        # own push-side quarantine skipping a malformed PARENT event while
        # still relaying its already-valid children (see sync_service.py's
        # module docstring). B genuinely never receives the product in this
        # batch, only the branch and the movement that depends on it.
        {"op": "outbox_delete_entity_type", "entity_type": "product"},
        {"op": "push"},
    ])

    [bootstrap] = run_accept_device(PY, device_b, relay_db, [{"op": "bootstrap"}])

    [cursor_before] = run_accept_device(PY, device_b, relay_db, [
        {"op": "raw_select", "sql": "SELECT last_seq FROM sync_cursor WHERE id=1"},
    ])
    assert cursor_before["rows"][0]["last_seq"] == 0

    [pull_result] = run_accept_device(PY, device_b, relay_db, [{"op": "pull"}])  # must not raise

    [cursor_after] = run_accept_device(PY, device_b, relay_db, [
        {"op": "raw_select", "sql": "SELECT last_seq FROM sync_cursor WHERE id=1"},
    ])
    # `relay_events` lives in the SHARED relay database, not either device's
    # own retail.db -- read it directly (FileRelay's own table, a pure
    # cross-process double with no application logic of its own; reading it
    # here proves nothing about the system under test by itself, only gives
    # the independent "how far could the cursor possibly have gone" figure
    # to compare the REAL cursor read above against).
    import sqlite3
    relay_conn = sqlite3.connect(str(relay_db))
    try:
        relay_max_seq = relay_conn.execute("SELECT MAX(seq) FROM relay_events").fetchone()[0]
    finally:
        relay_conn.close()
    # THE decisive check this pass adds over the delivered suite's own
    # equivalent test: the cursor genuinely moved, read directly from the
    # database -- not inferred from "pull_once() didn't raise".
    assert cursor_after["rows"][0]["last_seq"] == relay_max_seq, (
        "the cursor did not advance to the end of the batch despite a "
        "quarantined event inside it -- a device stuck here re-pulls the "
        "SAME failing range forever, exactly wave A's worst defect"
    )
    assert cursor_after["rows"][0]["last_seq"] > cursor_before["rows"][0]["last_seq"]

    cnt = run_accept_device(PY, device_b, relay_db, [{"op": "quarantine_count"}])[0]["count"]
    assert cnt == 1, "the orphaned movement was not parked"

    # The OTHER event in the same batch (the branch) still landed -- it was
    # not blocked by the orphaned movement ahead of or behind it.
    b_branch = run_accept_device(PY, device_b, relay_db, [
        {"op": "branch_by_uid", "uid": op_buid},
    ])[0]["branch"]
    assert b_branch is not None, "a sibling event in the same batch was wedged by the quarantined one"

    # The product legitimately arrives on a later pull.
    conn_events = run_accept_device(PY, device_a, relay_db, [
        {"op": "raw_select", "sql": "SELECT id, sku, barcode, name, category_id, supplier_id, cost_price, "
                                     "sell_price, tax_rate, unit, reorder_level, reorder_method, status "
                                     "FROM products WHERE id=?", "params": [pid]},
    ])[0]["rows"][0]
    import sqlite3
    import uuid
    conn = sqlite3.connect(str(relay_db))
    conn.execute(
        "INSERT INTO relay_events (payload) VALUES (?)",
        (json.dumps({
            "id": str(uuid.uuid4()), "entity_type": "product", "entity_id": pid,
            "event_type": "create", "created_at": "2026-01-01T00:00:00+00:00",
            "payload": dict(conn_events),
        }),),
    )
    conn.commit()
    conn.close()

    run_accept_device(PY, device_b, relay_db, [{"op": "pull"}])
    cnt_after = run_accept_device(PY, device_b, relay_db, [{"op": "quarantine_count"}])[0]["count"]
    assert cnt_after == 0, "resolved movement was not cleared from quarantine"

    bal = run_accept_device(PY, device_b, relay_db, [
        {"op": "get_balance", "company_id": bootstrap["company_id"], "product_id": pid,
         "branch_id": b_branch["id"]},
    ])[0]
    assert bal["quantity_on_hand"] == 60.0, "the parked movement was not applied once its product arrived"


def test_a_batch_delivered_in_reversed_order_still_converges_in_one_pull(tmp_path):
    import sqlite3

    device_a = tmp_path / "device_a"
    device_b = tmp_path / "device_b"
    relay_db = tmp_path / "relay.db"

    [shop] = run_accept_device(PY, device_a, relay_db, [
        {"op": "create_shop", "price": 12.0, "initial_stock": 0},
    ])
    pid = shop["product_id"]

    _, branch = run_accept_device(PY, device_a, relay_db, [
        {"op": "login", "email": shop["email"], "password": shop["password"]},
        {"op": "create_branch", "name": "Reorder Test Branch", "address": "", "phone": ""},
    ])
    assert branch["status_code"] == 200, branch["body"]

    run_accept_device(PY, device_a, relay_db, [
        {"op": "login", "email": shop["email"], "password": shop["password"]},
        {"op": "adjust_stock", "product_id": pid, "quantity": 80, "reason": "Opening, reorder test",
         "branch_id": branch["branch_id"]},
    ])

    # Capture A's REAL outbox rows verbatim (never pushed through the normal
    # push() path) -- exact real payloads a real writer produced, in their
    # ordinary causal order: product, then branch, then movement.
    [outbox] = run_accept_device(PY, device_a, relay_db, [
        {"op": "raw_select",
         "sql": "SELECT entity_type, entity_id, event_type, payload FROM sync_outbox ORDER BY rowid"},
    ])
    rows = outbox["rows"]
    by_type = {r["entity_type"]: r for r in rows}
    assert set(by_type) == {"product", "branch", "inventory_movement"}, by_type

    # Deliver them to a FRESH relay in DELIBERATELY REVERSED order: the
    # movement first (before its product AND before its branch), branch
    # second, product third.
    conn = sqlite3.connect(str(relay_db))
    for entity_type in ("inventory_movement", "branch", "product"):
        r = by_type[entity_type]
        conn.execute(
            "INSERT INTO relay_events (payload) VALUES (?)",
            (json.dumps({
                "entity_type": r["entity_type"], "entity_id": r["entity_id"],
                "event_type": r["event_type"], "payload": json.loads(r["payload"]),
            }),),
        )
    conn.commit()
    conn.close()

    [bootstrap] = run_accept_device(PY, device_b, relay_db, [{"op": "bootstrap"}])
    [pull_result] = run_accept_device(PY, device_b, relay_db, [{"op": "pull"}])  # ONE pull, must not raise

    # Converged in the SAME pull -- the quarantine/retry mechanism inside
    # apply_pull_result resolved the movement after the batch's own events
    # (branch, product) had already landed, all within this one call.
    cnt = run_accept_device(PY, device_b, relay_db, [{"op": "quarantine_count"}])[0]["count"]
    assert cnt == 0, "reversed-order batch did not converge within a single pull"

    b_branch = run_accept_device(PY, device_b, relay_db, [
        {"op": "branch_by_uid", "uid": by_type["branch"]["entity_id"]},
    ])[0]["branch"]
    assert b_branch is not None

    bal = run_accept_device(PY, device_b, relay_db, [
        {"op": "get_balance", "company_id": bootstrap["company_id"], "product_id": pid,
         "branch_id": b_branch["id"]},
    ])[0]
    assert bal["quantity_on_hand"] == 80.0, (
        "reversed-order batch converged to the wrong balance"
    )

    # Tier 1 resolved (the branch had already been applied earlier in this
    # SAME batch by the time the movement was retried) -- zero fallback
    # warnings, proving reordering-within-a-batch is the ORDINARY case, not
    # one that silently degrades to "filed under the wrong branch".
    warnings = pull_result.get("_log_warnings") or []
    assert warnings == [], f"reversed-order-but-still-fully-present batch triggered a branch fallback: {warnings}"

    [drift] = run_accept_device(PY, device_b, relay_db, [
        {"op": "compute_drift", "company_id": bootstrap["company_id"]},
    ])
    print(f"ACCEPTANCE compute_drift(reversed-order test, device=B) -> {drift['drift']!r}")
    assert drift["drift"] == [], drift["drift"]


def test_applying_the_same_batch_three_times_is_byte_identical(tmp_path):
    device_a = tmp_path / "device_a"
    device_b = tmp_path / "device_b"
    relay_db = tmp_path / "relay.db"

    [shop] = run_accept_device(PY, device_a, relay_db, [
        {"op": "create_shop", "price": 6.0, "initial_stock": 0},
    ])

    _, branch = run_accept_device(PY, device_a, relay_db, [
        {"op": "login", "email": shop["email"], "password": shop["password"]},
        {"op": "create_branch", "name": "Replay Test Branch", "address": "", "phone": ""},
    ])
    assert branch["status_code"] == 200, branch["body"]

    run_accept_device(PY, device_a, relay_db, [
        {"op": "login", "email": shop["email"], "password": shop["password"]},
        {"op": "adjust_stock", "product_id": shop["product_id"], "quantity": 45,
         "reason": "Opening, replay test", "branch_id": branch["branch_id"]},
    ])

    [outbox] = run_accept_device(PY, device_a, relay_db, [
        {"op": "raw_select",
         "sql": "SELECT entity_type, entity_id, event_type, payload FROM sync_outbox ORDER BY rowid"},
    ])
    events = [
        {"entity_type": r["entity_type"], "entity_id": r["entity_id"],
         "event_type": r["event_type"], "payload": json.loads(r["payload"])}
        for r in outbox["rows"]
    ]
    assert len(events) == 3  # product, branch, movement

    [bootstrap] = run_accept_device(PY, device_b, relay_db, [{"op": "bootstrap"}])

    # Apply the IDENTICAL batch three times, across a REAL process boundary
    # each time (a fresh subprocess per call) -- a replayed relay delivery,
    # a re-pulled cursor range, or (as reproduced literally here) a retried
    # network request that Owner had already durably applied.
    run_accept_device(PY, device_b, relay_db, [
        {"op": "apply_result_raw", "events": events, "cursor": 999},
    ])
    snapshot_1 = _full_snapshot(device_b, relay_db)

    run_accept_device(PY, device_b, relay_db, [
        {"op": "apply_result_raw", "events": events, "cursor": 999},
    ])
    snapshot_2 = _full_snapshot(device_b, relay_db)

    run_accept_device(PY, device_b, relay_db, [
        {"op": "apply_result_raw", "events": events, "cursor": 999},
    ])
    snapshot_3 = _full_snapshot(device_b, relay_db)

    assert snapshot_2 == snapshot_1, (
        "replay #2 changed the database -- full ordered snapshot diverged:\n"
        f"after 1st apply: {snapshot_1}\nafter 2nd apply: {snapshot_2}"
    )
    assert snapshot_3 == snapshot_1, (
        "replay #3 changed the database -- full ordered snapshot diverged:\n"
        f"after 1st apply: {snapshot_1}\nafter 3rd apply: {snapshot_3}"
    )

    # Sanity: the snapshot is not TRIVIALLY equal because nothing landed --
    # the movement and its balance update genuinely happened exactly once.
    assert len(snapshot_1["inventory_movements"]) == 1
    assert snapshot_1["inventory_balances"][0]["quantity_on_hand"] == 45.0
    assert snapshot_1["sync_apply_quarantine"] == []

    [drift] = run_accept_device(PY, device_b, relay_db, [
        {"op": "compute_drift", "company_id": bootstrap["company_id"]},
    ])
    print(f"ACCEPTANCE compute_drift(byte-identical-replay test, device=B) -> {drift['drift']!r}")
    assert drift["drift"] == [], drift["drift"]
