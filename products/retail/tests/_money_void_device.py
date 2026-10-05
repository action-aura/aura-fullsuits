"""Aura Retail -- F3b payment-void multi-process harness: one device's process.

Run as a bare subprocess (never imported, never collected by pytest -- the
leading underscore keeps it out of default collection), the same shape as
_stock_sync_device.py:

    python _money_void_device.py <app_data_dir> <relay_db_path> <actions_path> <output_path>

Why a separate file instead of a new op in _stock_sync_device.py: that
harness has a large, already-pinned op vocabulary used by other suites, and
what the void tests need on top of it is small and generic -- an `http` op
(any retail route as the logged-in user) and a `sql` op (read or write this
device's own retail.db). Everything else here is copied from it in behaviour.

Boots a REAL Flask app fresh inside THIS process with `AURA_APP_DATA` pointed
at `app_data_dir` (see _stock_sync_harness.py's "AURA_APP_DATA trap" for why
two devices cannot share a process). Reads a JSON list of `{"op": ...}` dicts,
runs them in order, writes the JSON list of results, exits 0. Any exception
aborts the whole batch non-zero with a traceback on stderr.

THE SYNC SERVICE IS BUILT THE WAY app.py BUILDS IT, INCLUDING
`install_void_reversal_sweep` -- that is the F3b post-pull sweep. A harness
service without it would let the "void arrives before the payment's journal"
test pass or fail for a reason production does not share. The install line
itself is pinned against app.py by retail_payment_void_twodevice_test.py
(`test_app_py_installs_the_sweep_on_both_retail_sync_services`), because this
file cannot exercise app.py's own wiring.

OPS
  create_shop   {"price":.., "initial_stock":..} -> {email, password, company_id, product_id}
  bootstrap     {} -> {email, password, company_id}   (fresh device, no product)
  login         {"email":.., "password":..}
  create_customer {"name":..} -> {status_code, customer_id}
  sell_credit   {"product_id":.., "quantity":.., "customer_id":.., "amount_paid":..}
  customer_payment {"customer_id":.., "amount":..}
  http          {"method":.., "path":.., "json": optional} -> {status_code, body}
  sql           {"q":.., "params": [..], "commit": optional} -> {rows}
  trial_balance {} -> {status_code, body}
  push / pull   {}
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
    seed_active_license(app_data_dir, product_code="AURA_RETAIL", platform="WINDOWS")

    import app as _app_module
    flask_app = _app_module.init_app()
    flask_app.config["TESTING"] = True

    from api.retail_api import _ensure_credit_schema, install_void_reversal_sweep, install_party_balance_refresh
    from commercial_runtime.identity.registry_db import get_conn as registry_conn
    from commercial_runtime.security.passwords import hash_password
    from commercial_runtime.sync.sync_service import SyncService, local_company_id_from_registry
    from database.schema import get_retail_conn, record_or_refresh_stock_exception

    from _stock_sync_harness import FileRelay

    relay = FileRelay(relay_db_path)
    service = SyncService(
        client_factory=lambda: relay,
        get_conn=get_retail_conn,
        local_company_id_provider=local_company_id_from_registry,
        local_ensure_schema=_ensure_credit_schema,
        stock_exception_recorder=record_or_refresh_stock_exception,
    )
    install_void_reversal_sweep(service)
    install_party_balance_refresh(service)  # v44: same wiring app.py gives both retail sync services

    API = "/api/sub/retail"
    client = flask_app.test_client()
    results: list = []

    def _login(email: str, password: str) -> None:
        r = client.post("/api/auth/login", json={"email": email, "password": password})
        if r.status_code != 200:
            raise RuntimeError(f"login failed: {r.status_code} {r.get_json()}")

    def _new_admin(action: dict):
        company_id = str(uuid.uuid4())
        email = action.get("email") or f"device-{uuid.uuid4().hex[:8]}@test.local"
        password = action.get("password", "DeviceVoidPW1")
        conn = registry_conn()
        conn.execute(
            "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, "
            "require_password_change) VALUES (?,?,?,?,?,?,?,0)",
            (str(uuid.uuid4()), company_id, "EMP-0001", email, hash_password(password), "admin", "active"),
        )
        conn.commit()
        conn.close()
        _login(email, password)
        return company_id, email, password

    actions = json.loads(Path(actions_path).read_text(encoding="utf-8"))
    for action in actions:
        op = action["op"]

        if op == "create_shop":
            company_id, email, password = _new_admin(action)
            price = action.get("price", 100.0)
            p = client.post(f"{API}/products", json={
                "name": "Void Widget", "sku": f"VW-{uuid.uuid4().hex[:8]}",
                "cost_price": price / 2, "sell_price": price, "tax_rate": 0,
                "initial_stock": action.get("initial_stock", 50),
            })
            if p.status_code != 200:
                raise RuntimeError(f"create_shop product creation failed: {p.status_code} {p.get_json()}")
            results.append({"email": email, "password": password, "company_id": company_id,
                            "product_id": p.get_json()["data"]["id"]})

        elif op == "bootstrap":
            company_id, email, password = _new_admin(action)
            results.append({"email": email, "password": password, "company_id": company_id})

        elif op == "login":
            _login(action["email"], action["password"])
            results.append({"ok": True})

        elif op == "create_customer":
            r = client.post(f"{API}/customers", json={"name": action.get("name", "Void Test Customer")})
            customer_id = None
            if r.status_code == 200:
                customer_id = r.get_json()["data"]["id"]
                conn = get_retail_conn()
                try:
                    conn.execute("UPDATE customers SET credit_mode=?, credit_limit=? WHERE id=?",
                                 ("unlimited", 1000000, customer_id))
                    conn.commit()
                finally:
                    conn.close()
            results.append({"status_code": r.status_code, "customer_id": customer_id})

        elif op == "sell_credit":
            r = client.post(f"{API}/sales", json={
                "items": [{"product_id": action["product_id"], "quantity": action["quantity"]}],
                "payment_method": "credit", "customer_id": action["customer_id"],
                "amount_paid": action.get("amount_paid", 0), "idempotency_key": str(uuid.uuid4()),
            })
            results.append({"status_code": r.status_code, "body": r.get_json()})

        elif op == "customer_payment":
            r = client.post(f"{API}/customers/{action['customer_id']}/payments", json={
                "amount": action["amount"], "method": action.get("method", "cash"),
            })
            results.append({"status_code": r.status_code, "body": r.get_json()})

        elif op == "http":
            m = getattr(client, action.get("method", "get").lower())
            r = m(f"{API}{action['path']}", json=action.get("json"))
            results.append({"status_code": r.status_code, "body": r.get_json(silent=True)})

        elif op == "sql":
            conn = get_retail_conn()
            try:
                rows = [dict(x) for x in conn.execute(action["q"], tuple(action.get("params", []))).fetchall()]
                if action.get("commit"):
                    conn.commit()
            finally:
                conn.close()
            results.append({"rows": rows})

        elif op == "trial_balance":
            r = client.get(f"{API}/reports/trial-balance")
            results.append({"status_code": r.status_code, "body": r.get_json()})

        elif op == "push":
            service.push_once()
            results.append({"ok": True})

        elif op == "pull":
            service.pull_once()
            results.append({"ok": True})

        else:
            raise ValueError(f"unknown device action op: {op!r}")

    Path(output_path).write_text(json.dumps(results, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
