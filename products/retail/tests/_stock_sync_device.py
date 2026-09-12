"""Aura Retail -- Phase 5 wave B multi-process harness: one device's process.

Run as a bare subprocess (never imported, never collected by pytest -- the
leading underscore keeps pytest's own default collection pattern from
picking it up, matching every other `_`-prefixed helper module in this
directory):

    python _stock_sync_device.py <app_data_dir> <relay_db_path> <actions_path> <output_path>

Boots a REAL Flask app (`app.init_app()`, exactly like every other route-
level test file in this suite) fresh inside THIS process, with
`AURA_APP_DATA` pointed at `app_data_dir` -- see _stock_sync_harness.py's own
module docstring ("the AURA_APP_DATA trap") for why this has to be a
separate OS process rather than a second boot inside the caller's own.
Reads `actions_path` (a JSON list of `{"op": ..., ...}` dicts, executed in
order against this one device), writes a JSON list of per-action results to
`output_path`, and exits 0. Any exception aborts the WHOLE batch with a
non-zero exit and a traceback on stderr -- `run_device()` in
_stock_sync_harness.py surfaces that verbatim rather than letting a broken
action look like "the device did nothing, everything downstream found
nothing to check".

OP VOCABULARY
-------------
HTTP-route ops (need a logged-in `client` -- `login` must be the first
action of any invocation that uses one of these, UNLESS `create_shop` is
that first action, which logs in as part of creating the company):

  create_shop      {"op": "create_shop", "email":.., "password":.., "price": 100.0}
                    -> {"company_id":.., "product_id":.., "branch_id":.., "branch_uid":..}
  login            {"op": "login", "email":.., "password":..} -> {"ok": true}
  create_product   {"op": "create_product", "name":.., "sku":.., "price":.., "initial_stock":..}
                    -> {"product_id":..}
  sell             {"op": "sell", "product_id":.., "quantity":.., "branch_id": optional}
                    -> {"status_code":.., "body": {...}}
  adjust_stock     {"op": "adjust_stock", "product_id":.., "quantity":.., "reason":.., "branch_id": optional}
                    -> {"status_code":.., "body": {...}}
  resolve_exception {"op": "resolve_exception", "exception_id":.., "note":..,
                     "counted_quantity": optional}
                    -> {"status_code":.., "body": {...}}
                    (launch-readiness Phase 7 stage 7d-ii, POST .../inventory/
                    stock-exceptions/<id>/resolve -- see retail_api.py's
                    `resolve_stock_exception`. `body["movement_uid"]`, when a
                    counted_quantity was supplied and actually changed the
                    balance, is the SAME uid `movement_count_by_uid` below can
                    look for on the OTHER device once it pulls -- the direct,
                    id-based proof that a specific correction travelled,
                    rather than an inference from a balance number that could
                    coincidentally match for the wrong reason.)
  create_branch    {"op": "create_branch", "name":.., "address":.., "phone":..}
                    -> {"status_code":.., "body": {...}, "branch_id":.., "branch_uid":..}
                    (branch_id/branch_uid are looked up from `branches` after
                    the route responds, since the route's own JSON body only
                    ever returns `{"id": ...}` -- see retail_api.py's
                    `create_branch`. An OPERATOR-created branch queues a real
                    `branch`/create sync event, unlike `_default_branch`'s
                    self-heal, which deliberately queues none -- see that
                    function's own "Wave B, CORRECTED" comment -- so this is
                    the only op in this vocabulary that produces a branch
                    uid two devices can ever agree names the same place.)

Non-HTTP ops (operate directly on this device's own retail.db / SyncService,
no login needed -- exactly mirroring how wave A's own `install_b` fixture
drives `SyncService` methods directly rather than through a route):

  push             {"op": "push"} -> {"ok": true}
  pull             {"op": "pull"} -> {"ok": true}
  compute_drift    {"op": "compute_drift", "company_id":..} -> {"drift": [...]}
  get_balance      {"op": "get_balance", "company_id":.., "product_id":.., "branch_id":..}
                    -> {"quantity_on_hand": float|None}
  quarantine_count {"op": "quarantine_count"} -> {"count": int}
  branch_by_uid    {"op": "branch_by_uid", "uid":..} -> {"branch": dict|None}
  branch_count     {"op": "branch_count", "company_id":..} -> {"count": int}
                    (COUNT(*) FROM branches WHERE company_id=? -- the direct
                    regression check for the duplicate-branch identity bug:
                    a company that only ever self-healed or synced ONE
                    physical branch must show exactly 1, never 2.)
  movement_count_by_uid {"op": "movement_count_by_uid", "uid":..} -> {"count": int}
  open_exception_id {"op": "open_exception_id", "company_id":.., "product_id":..}
                    -> {"id": str|None}
                    (the OPEN (resolved_at_utc IS NULL) stock_exceptions row's
                    own id for this company/product, or None if there isn't
                    one -- `stock_exceptions` is never itself a synced table
                    (each device's apply site writes its own rows from what
                    IT observes), so this reads the local row directly rather
                    than resolving it through any HTTP route.)
  outbox_delete_entity_type {"op": "outbox_delete_entity_type", "entity_type":..} -> {"deleted": int}
  cash_session_open {"op": "cash_session_open", "branch_id":.., "terminal_id":..}
                    -> {"status_code":.., "body": {...}}
  cash_session_status {"op": "cash_session_status"} -> {"open": bool, "session": dict|None}
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path


def main() -> None:
    app_data_dir, relay_db_path, actions_path, output_path = sys.argv[1:5]

    tests_dir = Path(__file__).resolve().parent
    product_dir = tests_dir.parent           # products/retail
    backend_dir = product_dir / "backend"
    suite_root = product_dir.parent.parent   # repo root
    for p in (str(suite_root), str(backend_dir), str(tests_dir)):
        if p not in sys.path:
            sys.path.insert(0, p)

    os.environ["AURA_STANDALONE"] = "1"
    os.environ["AURA_BUNDLE_DIR"] = str(backend_dir)
    os.environ["AURA_APP_DATA"] = app_data_dir
    os.environ.pop("AURA_DEV", None)

    Path(app_data_dir, "database", "subsystems").mkdir(parents=True, exist_ok=True)

    from commercial_runtime.licensing_contracts.test_support import seed_active_license
    # Idempotent (LicenseStateRepository.save() upserts) -- safe to call on
    # every invocation, including the Nth relaunch of an already-onboarded
    # device, not just the very first.
    seed_active_license(app_data_dir, product_code="AURA_RETAIL", platform="WINDOWS")

    import app as _app_module
    flask_app = _app_module.init_app()
    flask_app.config["TESTING"] = True

    from api.retail_api import _ensure_credit_schema
    from commercial_runtime.identity.registry_db import get_conn as registry_conn
    from commercial_runtime.security.passwords import hash_password
    from commercial_runtime.sync.sync_service import SyncService, local_company_id_from_registry
    from core.retail import stock_reconciliation
    from database.schema import get_retail_conn, record_or_refresh_stock_exception

    from _stock_sync_harness import FileRelay

    relay = FileRelay(relay_db_path)
    # Mirrors app.py's OWN production wiring exactly (local_company_id_
    # provider, local_ensure_schema, AND -- launch-readiness Phase 7 stage
    # 7d-i, extended to a second caller in 7d-iii -- stock_exception_
    # recorder) -- see that file's own SyncService construction comment.
    # Using anything narrower here would let this harness silently dodge
    # either the lazy-schema bug that comment describes OR the "the
    # resolution movement crosses devices" proof this file's own
    # test_a_resolution_movement_reaches_the_other_device (in
    # retail_oversell_exception_test.py) depends on this device actually
    # recording an exception via `open_exception_id` below -- a bare
    # SyncService with no `stock_exception_recorder` silently records
    # nothing, by design (see `_record_or_refresh_stock_exception`'s own
    # docstring in sync_service.py).
    service = SyncService(
        client_factory=lambda: relay,
        get_conn=get_retail_conn,
        local_company_id_provider=local_company_id_from_registry,
        local_ensure_schema=_ensure_credit_schema,
        stock_exception_recorder=record_or_refresh_stock_exception,
    )

    API = "/api/sub/retail"
    client = flask_app.test_client()
    results: list = []

    def _login(email: str, password: str) -> None:
        r = client.post("/api/auth/login", json={"email": email, "password": password})
        if r.status_code != 200:
            raise RuntimeError(f"login failed: {r.status_code} {r.get_json()}")

    actions = json.loads(Path(actions_path).read_text(encoding="utf-8"))
    for action in actions:
        op = action["op"]

        if op == "create_shop":
            company_id = str(uuid.uuid4())
            email = action.get("email") or f"device-{uuid.uuid4().hex[:8]}@test.local"
            password = action.get("password", "DeviceStockPW1")
            conn = registry_conn()
            conn.execute(
                "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, "
                "require_password_change) VALUES (?,?,?,?,?,?,?,0)",
                (str(uuid.uuid4()), company_id, "EMP-0001", email, hash_password(password), "admin", "active"),
            )
            conn.commit()
            conn.close()
            _login(email, password)
            price = action.get("price", 100.0)
            p = client.post(f"{API}/products", json={
                "name": action.get("product_name", "Stock Sync Widget"),
                "sku": f"SSW-{uuid.uuid4().hex[:8]}",
                "cost_price": price / 2, "sell_price": price, "tax_rate": 0,
                "initial_stock": action.get("initial_stock", 5000),
            })
            if p.status_code != 200:
                raise RuntimeError(f"create_shop product creation failed: {p.status_code} {p.get_json()}")
            product_id = p.get_json()["data"]["id"]
            conn = get_retail_conn()
            row = conn.execute(
                "SELECT id, uid FROM branches WHERE company_id=? ORDER BY id LIMIT 1", (company_id,)
            ).fetchone()
            conn.close()
            results.append({
                "email": email, "password": password, "company_id": company_id,
                "product_id": product_id, "branch_id": row["id"] if row else None,
                "branch_uid": row["uid"] if row else None,
            })

        elif op == "login":
            _login(action["email"], action["password"])
            results.append({"ok": True})

        elif op == "bootstrap":
            # A genuinely fresh receiving device: an admin and a company, but
            # NO product of its own -- unlike `create_shop`, which is always
            # the ORIGINATING device of a shared catalogue in these tests.
            # `_default_branch` is never called by anything this action
            # does, so this device self-heals no branch of its own either --
            # its first branch is whichever one arrives by sync, exactly
            # like `_resolve_branch_id`'s own docstring describes for a
            # brand-new install's tier-1 case.
            company_id = str(uuid.uuid4())
            email = action.get("email") or f"device-{uuid.uuid4().hex[:8]}@test.local"
            password = action.get("password", "DeviceStockPW1")
            conn = registry_conn()
            conn.execute(
                "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, "
                "require_password_change) VALUES (?,?,?,?,?,?,?,0)",
                (str(uuid.uuid4()), company_id, "EMP-0001", email, hash_password(password), "admin", "active"),
            )
            conn.commit()
            conn.close()
            _login(email, password)
            results.append({"email": email, "password": password, "company_id": company_id})

        elif op == "create_product":
            price = action.get("price", 100.0)
            p = client.post(f"{API}/products", json={
                "name": action.get("name", "Stock Sync Widget"),
                "sku": action.get("sku", f"SSW-{uuid.uuid4().hex[:8]}"),
                "cost_price": price / 2, "sell_price": price, "tax_rate": 0,
                "initial_stock": action.get("initial_stock", 0),
            })
            results.append({"status_code": p.status_code,
                             "product_id": (p.get_json().get("data") or {}).get("id") if p.status_code == 200 else None,
                             "body": p.get_json()})

        elif op == "sell":
            body = {"items": [{"product_id": action["product_id"], "quantity": action["quantity"]}],
                    "payment_method": action.get("payment_method", "cash"),
                    "idempotency_key": str(uuid.uuid4())}
            if action.get("branch_id") is not None:
                body["branch_id"] = action["branch_id"]
            r = client.post(f"{API}/sales", json=body)
            results.append({"status_code": r.status_code, "body": r.get_json()})

        elif op == "adjust_stock":
            body = {"quantity": action["quantity"], "reason": action.get("reason", "Device stock-sync test")}
            if action.get("branch_id") is not None:
                body["branch_id"] = action["branch_id"]
            r = client.post(f"{API}/products/{action['product_id']}/stock-adjust", json=body)
            results.append({"status_code": r.status_code, "body": r.get_json()})

        elif op == "resolve_exception":
            body = {"note": action["note"]}
            if action.get("counted_quantity") is not None:
                body["counted_quantity"] = action["counted_quantity"]
            r = client.post(f"{API}/inventory/stock-exceptions/{action['exception_id']}/resolve", json=body)
            results.append({"status_code": r.status_code, "body": r.get_json()})

        elif op == "create_branch":
            r = client.post(f"{API}/branches", json={
                "name": action["name"], "address": action.get("address", ""), "phone": action.get("phone", ""),
            })
            branch_id = None
            branch_uid = None
            if r.status_code == 200:
                branch_id = (r.get_json().get("data") or {}).get("id")
                conn = get_retail_conn()
                try:
                    row = conn.execute("SELECT uid FROM branches WHERE id=?", (branch_id,)).fetchone()
                finally:
                    conn.close()
                branch_uid = row["uid"] if row else None
            results.append({"status_code": r.status_code, "body": r.get_json(),
                             "branch_id": branch_id, "branch_uid": branch_uid})

        elif op == "push":
            service.push_once()
            results.append({"ok": True})

        elif op == "pull":
            service.pull_once()
            results.append({"ok": True})

        elif op == "compute_drift":
            conn = get_retail_conn()
            try:
                drift = stock_reconciliation.compute_drift(conn, action["company_id"])
            finally:
                conn.close()
            results.append({"drift": drift})

        elif op == "get_balance":
            conn = get_retail_conn()
            try:
                row = conn.execute(
                    "SELECT quantity_on_hand FROM inventory_balances WHERE company_id=? AND product_id=? "
                    "AND branch_id=?",
                    (action["company_id"], action["product_id"], action["branch_id"]),
                ).fetchone()
            finally:
                conn.close()
            results.append({"quantity_on_hand": row["quantity_on_hand"] if row else None})

        elif op == "quarantine_count":
            conn = get_retail_conn()
            try:
                n = conn.execute("SELECT COUNT(*) AS c FROM sync_apply_quarantine").fetchone()["c"]
            finally:
                conn.close()
            results.append({"count": n})

        elif op == "branch_by_uid":
            conn = get_retail_conn()
            try:
                row = conn.execute("SELECT * FROM branches WHERE uid=?", (action["uid"],)).fetchone()
            finally:
                conn.close()
            results.append({"branch": dict(row) if row else None})

        elif op == "branch_count":
            conn = get_retail_conn()
            try:
                n = conn.execute(
                    "SELECT COUNT(*) AS c FROM branches WHERE company_id=?", (action["company_id"],)
                ).fetchone()["c"]
            finally:
                conn.close()
            results.append({"count": n})

        elif op == "movement_count_by_uid":
            conn = get_retail_conn()
            try:
                n = conn.execute(
                    "SELECT COUNT(*) AS c FROM inventory_movements WHERE uid=?", (action["uid"],)
                ).fetchone()["c"]
            finally:
                conn.close()
            results.append({"count": n})

        elif op == "open_exception_id":
            conn = get_retail_conn()
            try:
                row = conn.execute(
                    "SELECT id FROM stock_exceptions WHERE company_id=? AND product_id=? "
                    "AND resolved_at_utc IS NULL",
                    (action["company_id"], action["product_id"]),
                ).fetchone()
            finally:
                conn.close()
            results.append({"id": row["id"] if row else None})

        elif op == "outbox_delete_entity_type":
            conn = get_retail_conn()
            try:
                cur = conn.execute("DELETE FROM sync_outbox WHERE entity_type=?", (action["entity_type"],))
                conn.commit()
                deleted = cur.rowcount
            finally:
                conn.close()
            results.append({"deleted": deleted})

        elif op == "cash_session_open":
            r = client.post(f"{API}/cash-sessions/open", json={
                "branch_id": action["branch_id"], "opening_float": action.get("opening_float", 100.0),
                "terminal_id": action.get("terminal_id"),
            })
            results.append({"status_code": r.status_code, "body": r.get_json()})

        elif op == "cash_session_status":
            conn = get_retail_conn()
            try:
                row = conn.execute(
                    "SELECT * FROM cash_sessions WHERE status='open' ORDER BY id DESC LIMIT 1"
                ).fetchone()
            finally:
                conn.close()
            results.append({"open": row is not None, "session": dict(row) if row else None})

        else:
            raise ValueError(f"unknown device action op: {op!r}")

    Path(output_path).write_text(json.dumps(results, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
