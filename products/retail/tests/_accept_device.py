"""Adversarial-ACCEPTANCE-pass device process for wave B1 (stock-moving sync).

NOT part of the delivered suite and NOT a modification of any existing file --
a standalone extension of `_verify_wb1_device.py` (itself a standalone
extension of the builder's own `_stock_sync_device.py`), copied rather than
imported for the same survive-independently reason every other duplicate
helper module in this test directory states explicitly. Every existing op
this file's ancestors already support is reproduced here UNCHANGED; this file
only ADDS what this acceptance pass specifically needs and the two ancestors
do not offer:

  * `create_po` gains an optional `branch_id` -- the delivered/verify device
    scripts always let `receive_purchase_order` fall back to
    `_default_branch`, which is fine for a single-branch scenario but useless
    for proving PER-BRANCH correctness (this pass's requirement B) across an
    OPERATOR-created branch a delivery is deliberately routed to.
  * `apply_result_raw` -- calls `SyncService.apply_pull_result()` directly
    with a caller-supplied `{"events": [...], "cursor": N}` dict, exactly the
    technique retail_stock_sync_test.py's own
    `test_replaying_the_full_pull_result_twice_does_not_double_stock` already
    uses in-process (via `install_b`) to replay an already-applied batch --
    generalised here to a REAL separate OS process and to a 3x replay, so the
    acceptance pass can prove byte-identical convergence across a genuine
    process boundary rather than only within one Python interpreter.
  * A logging capture around every action: `sync_service.py`'s own
    `_resolve_branch_id` logs a WARNING (batched: see
    `_log_branch_fallback_summary`) exactly when the tier-2 default-branch
    fallback fires, and NEVER when tier-1 resolves. This pass's requirement D
    needs to prove BOTH directions -- the warning existing when the fallback
    fires, and its absence when it does not -- which requires reading the
    actual log output, not inferring it from a balance number. A small
    in-process `logging.Handler` attached to `commercial_runtime.sync.
    sync_service`'s own logger (the exact name `sync_service.py`'s
    `logging.getLogger(__name__)` resolves to when imported by its real
    dotted path, matching how `app.py`/`_verify_wb1_device.py` import it)
    captures every record emitted DURING each individual action and attaches
    it to that action's own result entry as `_log_warnings` -- so a test can
    assert on the log output of one specific `pull`, not the whole process.

Run as a bare subprocess, same contract as its ancestors:
    python _accept_device.py <app_data_dir> <relay_db_path> <actions_path> <output_path>
"""
from __future__ import annotations

import json
import logging
import os
import sys
import uuid
from pathlib import Path

_CAPTURED_LOGS: list = []


class _ListHandler(logging.Handler):
    """Appends the FORMATTED message (not the raw LogRecord, which is not
    JSON-serialisable) to a shared module-level list. Deliberately only ever
    attached once, at module import time -- see `main()`'s own attach call.
    """

    def emit(self, record: logging.LogRecord) -> None:
        _CAPTURED_LOGS.append(self.format(record))


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

    # Attach the capture handler to the EXACT logger sync_service.py's own
    # `logging.getLogger(__name__)` resolves to, now that the module has
    # actually been imported under its real dotted path above.
    _sync_logger = logging.getLogger("commercial_runtime.sync.sync_service")
    _sync_logger.setLevel(logging.WARNING)
    _handler = _ListHandler()
    _handler.setLevel(logging.WARNING)
    _sync_logger.addHandler(_handler)

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
        _log_start = len(_CAPTURED_LOGS)

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

        elif op == "create_supplier":
            r = client.post(f"{API}/suppliers", json={"name": action["name"]})
            results.append({"status_code": r.status_code, "body": r.get_json(),
                             "supplier_id": (r.get_json().get("data") or {}).get("id") if r.status_code == 200 else None})

        elif op == "create_po":
            payload = {"supplier_id": action["supplier_id"], "items": action["items"]}
            # ACCEPTANCE-PASS ADDITION (see module docstring): explicit
            # branch routing, so a delivery can be proven to land on a named
            # OPERATOR-created branch rather than whatever this device's own
            # `_default_branch` happens to resolve to.
            if action.get("branch_id") is not None:
                payload["branch_id"] = action["branch_id"]
            r = client.post(f"{API}/purchase-orders", json=payload)
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

        elif op == "apply_result_raw":
            # ACCEPTANCE-PASS ADDITION (see module docstring): applies a
            # caller-supplied pull result directly, bypassing the cursor --
            # deliberately callable more than once with the IDENTICAL events,
            # to prove replay convergence across a real process boundary
            # rather than only within one Python interpreter (as the
            # delivered suite's own `install_b`-based replay test already
            # does in-process).
            conn = get_retail_conn()
            try:
                service.apply_pull_result(conn, {"events": action["events"], "cursor": action["cursor"]})
                conn.commit()
            finally:
                conn.close()
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
                             "session_id": (r.get_json().get("data") or {}).get("id") if r.status_code == 200 else None})

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

        # Attach whatever this ONE action logged (WARNING+) to its own result
        # entry -- see module docstring's logging-capture paragraph. Every
        # branch above appends EXACTLY one entry, so results[-1] is always
        # this action's own.
        results[-1]["_log_warnings"] = _CAPTURED_LOGS[_log_start:]

    Path(output_path).write_text(json.dumps(results, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
