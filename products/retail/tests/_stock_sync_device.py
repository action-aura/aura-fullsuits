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

GL SYNC OPS (Wave 1, BOOKS-WAVE1-PLAN.md section 6, Builder D) -- additive,
never touching any op above. `retail_gl_two_device_sync_test.py` is the only
caller. A cross-device fact this file's own vocabulary has no dedicated op
for (a device's local sale id for a peer's synced sale uid; poking one
device's own `products.avg_cost` to diverge it from another's) is reached by
that test file connecting DIRECTLY to a device's own `retail.db` (or the
shared `relay.db`) between `run_device()` calls instead of a new op here --
the identical technique this suite already establishes (`retail_gl_
integration_test.py`'s own `_set_customer_credit` helper; this harness's own
`retail_stock_sync_test.py` hand-inserting a `relay_events` row directly) --
rather than growing this vocabulary past what BOOKS-WAVE1-PLAN.md section 6
actually names.

  pnl              {"op": "pnl", "start_date":.., "end_date":.., "branch_id": optional}
                    -> {"status_code":.., "body": {...}}  (GET .../reports/profit-and-loss)
  trial_balance    {"op": "trial_balance"} -> {"status_code":.., "body": {...}}
                    (GET .../reports/trial-balance -- `body["data"]["subledger"]`
                    is embedded in the SAME dict, see report_trial_balance's
                    own comment in retail_api.py)
  gl_census        {"op": "gl_census", "company_id":..}
                    -> {"count":.., "distinct_uids":.., "null_uids":..,
                        "uid_hash":..}  (sha256 of the sorted, newline-joined
                        non-null uid list -- a compact cross-device
                        fingerprint the two devices' P&L/trial-balance
                        convergence checks can compare directly)
  create_customer  {"op": "create_customer", "name":.., "credit_mode": "unlimited",
                     "credit_limit": 1000000}
                    -> {"status_code":.., "customer_id":.., "body": {...}}
                    (POST .../customers, then a raw UPDATE granting credit --
                    identical values to retail_gl_integration_test.py's own
                    `_set_customer_credit`, so a credit sale against this
                    customer is never refused for a credit-limit reason this
                    suite doesn't care about)
  sell_credit      {"op": "sell_credit", "product_id":.., "quantity":.., "customer_id":..,
                     "amount_paid": 0, "branch_id": optional}
                    -> {"status_code":.., "body": {...}}
                    (POST .../sales, payment_method='credit' -- the credit-
                    sale AR posting path, `gl.post_sale`'s `ar_amount` leg)
  return_sale      {"op": "return_sale", "sale_id":.., "items": [{"product_id":.., "quantity":..}]}
                    -> {"status_code":.., "body": {...}}  (POST .../returns)
  customer_payment {"op": "customer_payment", "customer_id":.., "amount":.., "method": "cash"}
                    -> {"status_code":.., "body": {...}}
                    (POST .../customers/<id>/payments -- `gl.post_customer_payment`)
  reset_cursor     {"op": "reset_cursor"} -> {"ok": true}
                    (raw `UPDATE sync_cursor SET last_seq=0` -- simulates a
                    cursor reset/re-pull-everything without waiting for a
                    real relay-url change to trigger `ensure_cursor_matches_
                    relay` on its own)
  switch_relay_url {"op": "switch_relay_url", "relay_url":..} -> {"ok": true}
                    (raw `UPDATE sync_cursor SET last_seq=0, relay_url=?` --
                    the SAME two-column reset `ensure_cursor_matches_relay`
                    itself performs on a genuine relay change, BOOKS-WAVE1-
                    PLAN.md section 7 checkpoint C5's "hub<->cloud switch".
                    Applied directly here, not through the real detection
                    path, because `_stock_sync_harness.FileRelay` -- a test
                    double, not the real `SyncRelayClient` -- exposes no
                    `base_url` for that path to key off; see `pull_once`'s
                    own `getattr(client, "base_url", None)` comment for why
                    that is the safe default for a client that cannot name
                    its own relay, not a gap this wave needs to close)
  gl_post_backdated {"op": "gl_post_backdated", "company_id":.., "branch_id": optional,
                      "business_date":.., "source_type": "adjustment", "currency": "JOD",
                      "lines": [{"account_code":.., "debit":.., "credit":.., "memo": optional}, ...],
                      "reference": optional, "memo": optional, "posted_by": optional}
                    -> {"journal_id":.., "uid":..}
                    (a direct `general_ledger.post_journal` call -- there is
                    no manual-journal HTTP route by design, BOOKS-WAVE1-
                    PLAN.md section 8's "out of scope" list -- for a test
                    that needs one backdated or hand-shaped journal without
                    a real sale/return/payment behind it)
  gl_reverse_last  {"op": "gl_reverse_last", "company_id":.., "memo": optional}
                    -> {"reversed_journal_id": int|None, "new_journal_id": int|None}
                    (this company's own highest-id `gl_journals` row, reversed
                    via `general_ledger.reverse_journal` -- None/None when the
                    company has posted nothing yet)
  insert_legacy_journal_raw {"op": "insert_legacy_journal_raw", "company_id":..,
                              "branch_id": optional, "business_date":..,
                              "source_type": "sale", "currency": "JOD",
                              "lines": [...], "reference": optional, "memo": optional,
                              "posted_by": optional}
                    -> {"journal_id":..}
                    (a RAW `INSERT INTO gl_journals` using the v38 column set
                    only -- `uid`/`source_uid` are never named, so both stay
                    NULL exactly like a genuine pre-v41 row -- see BOOKS-
                    WAVE1-PLAN.md section 6 trap #5: "Making 'legacy' journals
                    with the new code" is itself a fixture trap, so this
                    deliberately never calls `post_journal`, which would
                    always mint a uid)
  quarantine_rows  {"op": "quarantine_rows", "entity_type": optional}
                    -> {"rows": [{"entity_id":.., "entity_type":.., "event_type":..,
                                   "reason":.., "detail":..}, ...]}
  outbox_count_by_type {"op": "outbox_count_by_type", "entity_type":..} -> {"count": int}
"""
from __future__ import annotations

import hashlib
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
    from core.retail import general_ledger as gl
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
    # app.py gives BOTH retail sync services these two post-pull hooks. This
    # harness used to omit them, so a device that only PULLED kept a stale
    # `credit_balance` (a PO down payment pulled by B left B's supplier at 0
    # instead of -30) -- a fixture defect, not a product one: the production
    # wiring refreshes after every pull. Same wiring as _money_void_device.py.
    from api.retail_api import install_void_reversal_sweep, install_party_balance_refresh
    install_void_reversal_sweep(service)
    install_party_balance_refresh(service)

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
                    # REHEARSAL-1007 B8: POST /branches on a company with no branch now
                    # also creates the standard local-only "Main Branch" placeholder
                    # first. These two-computer scenarios were written for "the first
                    # branch created here IS the shop's one branch, and it syncs", so
                    # the harness drops that EMPTY placeholder to keep their shape.
                    # What that hides: the placeholder's own behaviour, which
                    # retail_rehearsal_b8_b9_branch_and_float_test.py owns.
                    conn.execute(
                        "DELETE FROM branches WHERE name='Main Branch' AND id<? AND company_id="
                        "(SELECT company_id FROM branches WHERE id=?) "
                        "AND id NOT IN (SELECT branch_id FROM inventory_balances WHERE branch_id IS NOT NULL) "
                        "AND id NOT IN (SELECT branch_id FROM sales WHERE branch_id IS NOT NULL)",
                        (branch_id, branch_id))
                    conn.commit()
                    row = conn.execute("SELECT uid FROM branches WHERE id=?", (branch_id,)).fetchone()
                finally:
                    conn.close()
                branch_uid = row["uid"] if row else None
            results.append({"status_code": r.status_code, "body": r.get_json(),
                             "branch_id": branch_id, "branch_uid": branch_uid})

        elif op == "http":
            # STOCK-BOOKS (crew B): a generic logged-in request, for routes this
            # vocabulary has no dedicated op for (stock transfers). Additive --
            # no existing op changes. {"op":"http","method":"POST","path":"/stock-transfers",
            # "json":{...}} -> {"status_code":.., "body":..}; `path` is relative to /api/sub/retail.
            r = client.open(f"{API}{action['path']}", method=action.get("method", "GET"),
                            json=action.get("json"))
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

        # ── Wave 1 (BOOKS-WAVE1-PLAN.md section 6, Builder D) -- GL sync ops,
        #    additive only, see this module's own docstring "GL SYNC OPS" ──

        elif op == "pnl":
            params = {"start_date": action["start_date"], "end_date": action["end_date"]}
            if action.get("branch_id") is not None:
                params["branch_id"] = action["branch_id"]
            r = client.get(f"{API}/reports/profit-and-loss", query_string=params)
            results.append({"status_code": r.status_code, "body": r.get_json()})

        elif op == "trial_balance":
            r = client.get(f"{API}/reports/trial-balance")
            results.append({"status_code": r.status_code, "body": r.get_json()})

        elif op == "gl_census":
            conn = get_retail_conn()
            try:
                rows = conn.execute(
                    "SELECT uid FROM gl_journals WHERE company_id=?", (action["company_id"],)
                ).fetchall()
            finally:
                conn.close()
            uids = [r["uid"] for r in rows]
            non_null_sorted = sorted(u for u in uids if u is not None)
            digest = hashlib.sha256("\n".join(non_null_sorted).encode("utf-8")).hexdigest()
            results.append({
                "count": len(uids),
                "distinct_uids": len(set(non_null_sorted)),
                "null_uids": sum(1 for u in uids if u is None),
                "uid_hash": digest,
            })

        elif op == "create_customer":
            r = client.post(f"{API}/customers", json={"name": action.get("name", "Sync Test Customer")})
            customer_id = None
            if r.status_code == 200:
                customer_id = r.get_json()["data"]["id"]
                conn = get_retail_conn()
                try:
                    conn.execute(
                        "UPDATE customers SET credit_mode=?, credit_limit=? WHERE id=?",
                        (action.get("credit_mode", "unlimited"), action.get("credit_limit", 1000000),
                         customer_id),
                    )
                    conn.commit()
                finally:
                    conn.close()
            results.append({"status_code": r.status_code, "customer_id": customer_id, "body": r.get_json()})

        elif op == "sell_credit":
            body = {
                "items": [{"product_id": action["product_id"], "quantity": action["quantity"]}],
                "payment_method": "credit",
                "customer_id": action["customer_id"],
                "amount_paid": action.get("amount_paid", 0),
                "idempotency_key": str(uuid.uuid4()),
            }
            if action.get("branch_id") is not None:
                body["branch_id"] = action["branch_id"]
            r = client.post(f"{API}/sales", json=body)
            results.append({"status_code": r.status_code, "body": r.get_json()})

        elif op == "return_sale":
            r = client.post(f"{API}/returns", json={
                "sale_id": action["sale_id"], "items": action["items"],
            })
            results.append({"status_code": r.status_code, "body": r.get_json()})

        elif op == "customer_payment":
            r = client.post(f"{API}/customers/{action['customer_id']}/payments", json={
                "amount": action["amount"], "method": action.get("method", "cash"),
            })
            results.append({"status_code": r.status_code, "body": r.get_json()})

        elif op == "reset_cursor":
            conn = get_retail_conn()
            try:
                conn.execute("UPDATE sync_cursor SET last_seq=0 WHERE id=1")
                conn.commit()
            finally:
                conn.close()
            results.append({"ok": True})

        elif op == "switch_relay_url":
            conn = get_retail_conn()
            try:
                conn.execute(
                    "UPDATE sync_cursor SET last_seq=0, relay_url=? WHERE id=1",
                    (action["relay_url"],),
                )
                conn.commit()
            finally:
                conn.close()
            results.append({"ok": True})

        elif op == "gl_post_backdated":
            conn = get_retail_conn()
            try:
                journal_id = gl.post_journal(
                    conn, action["company_id"], action.get("branch_id"),
                    action["business_date"], action.get("source_type", "adjustment"),
                    None, action.get("currency", "JOD"), action["lines"],
                    reference=action.get("reference"), memo=action.get("memo"),
                    posted_by=action.get("posted_by", "Device Test"),
                )
                conn.commit()
                row = conn.execute("SELECT uid FROM gl_journals WHERE id=?", (journal_id,)).fetchone()
            finally:
                conn.close()
            results.append({"journal_id": journal_id, "uid": row["uid"] if row else None})

        elif op == "gl_reverse_last":
            conn = get_retail_conn()
            try:
                last = conn.execute(
                    "SELECT id FROM gl_journals WHERE company_id=? ORDER BY id DESC LIMIT 1",
                    (action["company_id"],),
                ).fetchone()
                reversed_id = last["id"] if last else None
                new_id = None
                if reversed_id is not None:
                    new_id = gl.reverse_journal(conn, action["company_id"], reversed_id,
                                                 memo=action.get("memo"))
                    conn.commit()
            finally:
                conn.close()
            results.append({"reversed_journal_id": reversed_id, "new_journal_id": new_id})

        elif op == "insert_legacy_journal_raw":
            conn = get_retail_conn()
            try:
                cid_ = action["company_id"]
                currency = action.get("currency", "JOD")
                gl.ensure_default_chart(conn, cid_)
                clean_lines, _debit_total, _credit_total = gl.validated_lines(action["lines"], currency)
                cur = conn.execute(
                    "INSERT INTO gl_journals (company_id, branch_id, business_date, reference, memo, "
                    "source_type, source_id, currency, reversed_journal_id, posted_by) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (cid_, action.get("branch_id"), action["business_date"], action.get("reference"),
                     action.get("memo"), action.get("source_type", "sale"), None, currency, None,
                     action.get("posted_by", "System")),
                )
                journal_id = cur.lastrowid
                for code, debit, credit, line_memo in clean_lines:
                    acc = conn.execute(
                        "SELECT id FROM gl_accounts WHERE company_id=? AND code=?", (cid_, code)
                    ).fetchone()
                    conn.execute(
                        "INSERT INTO gl_journal_lines (journal_id, account_id, debit, credit, memo) "
                        "VALUES (?,?,?,?,?)",
                        (journal_id, acc["id"], float(debit), float(credit), line_memo),
                    )
                conn.commit()
            finally:
                conn.close()
            results.append({"journal_id": journal_id})

        elif op == "quarantine_rows":
            conn = get_retail_conn()
            try:
                sql = ("SELECT entity_id, entity_type, event_type, reason, detail "
                       "FROM sync_apply_quarantine")
                params = ()
                if action.get("entity_type") is not None:
                    sql += " WHERE entity_type=?"
                    params = (action["entity_type"],)
                rows = conn.execute(sql, params).fetchall()
            finally:
                conn.close()
            results.append({"rows": [dict(r) for r in rows]})

        elif op == "outbox_count_by_type":
            conn = get_retail_conn()
            try:
                n = conn.execute(
                    "SELECT COUNT(*) AS c FROM sync_outbox WHERE entity_type=?",
                    (action["entity_type"],),
                ).fetchone()["c"]
            finally:
                conn.close()
            results.append({"count": n})

        else:
            raise ValueError(f"unknown device action op: {op!r}")

    Path(output_path).write_text(json.dumps(results, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
