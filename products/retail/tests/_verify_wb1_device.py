"""Adversarial-verifier device process for wave B1 (stock-moving sync).

NOT part of the delivered suite -- a standalone extension of the builder's
own `_stock_sync_device.py` (which is left completely untouched; this file
is new, not a modification). Reuses the exact same boot sequence (real
Flask app, real AURA_APP_DATA, real SyncService wiring) and the exact same
subprocess-per-device model documented in `_stock_sync_harness.py`, but
adds the op vocabulary the delivered harness does not cover and this
verification needs: purchase orders/receiving, returns, closing a cash
drawer, listing branches, and a generic read-only SQL escape hatch --
needed to prove per-branch correctness, the Phase 4 drawer-survives-sync
guarantee, and a genuinely multi-branch, multi-hazard scenario end to end
through the real routes, across real OS processes.

Run as a bare subprocess, same contract as _stock_sync_device.py:
    python _verify_wb1_device.py <app_data_dir> <relay_db_path> <actions_path> <output_path>

Additional ops beyond _stock_sync_device.py's own vocabulary:
  create_supplier   {"op":"create_supplier","name":..} -> {"status_code":..,"supplier_id":..,"body":..}
  create_po         {"op":"create_po","supplier_id":..,"items":[{"product_id":,"quantity":,"unit_cost":}]}
                     -> {"status_code":..,"po_id":..,"body":..}
  receive_po        {"op":"receive_po","po_id":..} -> {"status_code":..,"body":..}
  create_return     {"op":"create_return","sale_id":..,"items":[{"product_id":,"quantity":}]}
                     -> {"status_code":..,"body":..}
  cash_session_close {"op":"cash_session_close","session_id":..,"closing_float_counted":..}
                     -> {"status_code":..,"body":..}
  list_branches     {"op":"list_branches","company_id":..} -> {"branches":[...]}
  raw_select        {"op":"raw_select","sql":..,"params":[...]} -> {"rows":[...]}
All ops from _stock_sync_device.py's own vocabulary are supported identically
(the dispatch loop below is a superset, copied rather than imported for the
same survive-independently reason every other duplicate helper module in
this suite states explicitly).
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
    product_dir = tests_dir.parent
    backend_dir = product_dir / "backend"
    suite_root = product_dir.parent.parent
    for p in (str(suite_root), str(backend_dir), str(tests_dir)):
        if p not in sys.path:
            sys.path.insert(0, p)

    os.environ["AURA_STANDALONE"] = "1"
    os.environ["AURA_BUNDLE_DIR"] = str(backend_dir)
    os.environ["AURA_APP_DATA"] = app_data_dir
    os.environ.pop("AURA_DEV", None)

    Path(app_data_dir, "database", "subsystems").mkdir(parents=True, exist_ok=True)

    from commercial_runtime.licensing_contracts.test_support import seed_active_license
    seed_active_license(app_data_dir, product_code="AURA_RETAIL", platform="WINDOWS")

    import app as _app_module
    flask_app = _app_module.init_app()
    flask_app.config["TESTING"] = True

    from api.retail_api import _ensure_credit_schema
    from commercial_runtime.identity.registry_db import get_conn as registry_conn
    from commercial_runtime.security.passwords import hash_password
    from commercial_runtime.sync.sync_service import SyncService, local_company_id_from_registry
    from core.retail import stock_reconciliation
    from database.schema import get_retail_conn

    from _stock_sync_harness import FileRelay

    relay = FileRelay(relay_db_path)
    service = SyncService(
        client_factory=lambda: relay,
        get_conn=get_retail_conn,
        local_company_id_provider=local_company_id_from_registry,
        local_ensure_schema=_ensure_credit_schema,
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
            results.append({"status_code": r.status_code, "body": r.get_json(),
                             "sale_id": (r.get_json().get("data") or {}).get("id") if r.status_code == 200 else None})

        elif op == "adjust_stock":
            body = {"quantity": action["quantity"], "reason": action.get("reason", "Device stock-sync test")}
            if action.get("branch_id") is not None:
                body["branch_id"] = action["branch_id"]
            r = client.post(f"{API}/products/{action['product_id']}/stock-adjust", json=body)
            results.append({"status_code": r.status_code, "body": r.get_json()})

        elif op == "create_branch":
            r = client.post(f"{API}/branches", json={
                "name": action["name"], "address": action.get("address", ""), "phone": action.get("phone", ""),
            })
            results.append({"status_code": r.status_code, "body": r.get_json(),
                             "branch_id": (r.get_json().get("data") or {}).get("id") if r.status_code == 200 else None})

        elif op == "create_supplier":
            r = client.post(f"{API}/suppliers", json={"name": action["name"]})
            results.append({"status_code": r.status_code, "body": r.get_json(),
                             "supplier_id": (r.get_json().get("data") or {}).get("id") if r.status_code == 200 else None})

        elif op == "create_po":
            r = client.post(f"{API}/purchase-orders", json={
                "supplier_id": action["supplier_id"], "items": action["items"],
            })
            results.append({"status_code": r.status_code, "body": r.get_json(),
                             "po_id": (r.get_json().get("data") or {}).get("id") if r.status_code == 200 else None})

        elif op == "receive_po":
            r = client.post(f"{API}/purchase-orders/{action['po_id']}/receive")
            results.append({"status_code": r.status_code, "body": r.get_json()})

        elif op == "create_return":
            body = {"sale_id": action["sale_id"], "items": action["items"]}
            r = client.post(f"{API}/returns", json=body)
            results.append({"status_code": r.status_code, "body": r.get_json()})

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

        elif op == "list_branches":
            conn = get_retail_conn()
            try:
                rows = conn.execute(
                    "SELECT id, company_id, name, uid, status FROM branches WHERE company_id=? ORDER BY id",
                    (action["company_id"],),
                ).fetchall()
            finally:
                conn.close()
            results.append({"branches": [dict(r) for r in rows]})

        elif op == "raw_select":
            conn = get_retail_conn()
            try:
                rows = conn.execute(action["sql"], action.get("params", [])).fetchall()
            finally:
                conn.close()
            results.append({"rows": [dict(r) for r in rows]})

        elif op == "movement_count_by_uid":
            conn = get_retail_conn()
            try:
                n = conn.execute(
                    "SELECT COUNT(*) AS c FROM inventory_movements WHERE uid=?", (action["uid"],)
                ).fetchone()["c"]
            finally:
                conn.close()
            results.append({"count": n})

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
            })
            results.append({"status_code": r.status_code, "body": r.get_json(),
                             "session_id": (r.get_json().get("data") or {}).get("id") if r.status_code == 200 else None,
                             "session_id_from_error": (r.get_json().get("data") or {}).get("session_id")
                             if r.status_code == 409 else None})

        elif op == "cash_session_close":
            body = {"closing_float_counted": action["closing_float_counted"]}
            r = client.post(f"{API}/cash-sessions/{action['session_id']}/close", json=body)
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
