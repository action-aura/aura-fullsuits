"""SYNC-BOOT Phase 2, END TO END: a computer that joins a shop's LAN hub AFTER the hub pruned its
site log is told to resync, takes a copy of the shop FROM THE HUB, and ends up holding the shop's
products, customers and sales AND able to sign the owner in -- with nothing mocked on the path.

WHAT IS REAL
  * the hub: this test process is the shop's main computer. It runs the real app (`app.init_app()`),
    real routes created the shop, its two SyncServices push through the real `LocalSiteRelayClient`
    into the real site log, and the real TLS listener (`listener.start_site_relay`) serves it with the
    bootstrap wiring `app.py` hands the production hub (`_hub_bootstrap_provider`, the board, the
    main-computer callback).
  * the hub's pruning: the real `run_housekeeping_sweep` with the hub's real consumer floors -- the
    SYNC-JOIN 0-floor stop-gap REMOVED inside this test only (`_hub_housekeeping_floors` is swapped
    for the floors alone). Nothing is deleted by hand.
  * the late device C: a separate OS process (`_accept_device.py`, `hub` option), the real
    `SyncRelayClient` over SPKI-pinned TLS signed by its own DPAPI device key, both streams, the same
    bootstrap hooks production wires. Its second run is its "restart": `init_app()` installs the staged
    copy before any database is opened.
  * signing: every computer holds a real Owner-signed lease; the copy's manifest and the main
    computer's re-announce are verified by the same code that guards production.

WHAT IS SIMULATED, said plainly
  * Owner's cloud does not exist here (LAN-only). Pruning is capped by the hub forwarder's
    `forwarded_to_seq`, so the test sets it to "everything forwarded", which is what a hub with a
    working cloud link reaches.
  * "device B", an earlier paired computer that has caught up, is a row in `site_paired_devices` with
    caught-up cursors plus one event it authored, not a second process. It exists so that the hub's
    own cursor advances (a hub never receives its own events back) and the prune watermark is > 0.

Run: pytest products/retail/tests/retail_sync_join_hub_bootstrap_e2e.py -q
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / "backend"
SUITE_ROOT = PRODUCT_DIR.parent.parent
TESTS_DIR = Path(__file__).resolve().parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR), str(TESTS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

LICENCE = "LIC-E2E-HUB-BOOT"
DATA = Path(tempfile.mkdtemp(prefix="aura_hubboot_a_"))
C_DIR = Path(tempfile.mkdtemp(prefix="aura_hubboot_c_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(
    AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA),
    AURA_SITE_RELAY_ENABLED="0", AURA_BACKUP_SCHEDULER="0",
    AURA_ACCEPT_SIGNED="1", AURA_ACCEPT_SIGNED_LICENCE=LICENCE)
for _k in ("AURA_DEV", "AURA_OWNER_LICENSING_URL", "AURA_SYNC_RELAY_URL"):
    os.environ.pop(_k, None)

import _accept_device as accept_device  # noqa: E402
from _accept_harness import run_accept_device  # noqa: E402
from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402

seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")
import app as app_module  # noqa: E402

flask_app = app_module.init_app()
flask_app.config["TESTING"] = True
accept_device._provision_signed_identity(str(DATA))

from api.retail_api import _ensure_credit_schema  # noqa: E402
from commercial_runtime.identity import device_context  # noqa: E402
from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.licensing_contracts.state_repository import LicenseStateRepository  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402
from commercial_runtime.sync.site_relay import listener, store  # noqa: E402
from commercial_runtime.sync.site_relay.local_client import LocalSiteRelayClient  # noqa: E402
from commercial_runtime.sync.sync_service import (  # noqa: E402
    REGISTRY_SYNC_ENTITY_TYPES, SyncService, local_company_id_from_registry)
from database import schema  # noqa: E402
from database.schema import get_retail_conn  # noqa: E402

API = "/api/sub/retail"


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)
    shutil.rmtree(C_DIR, ignore_errors=True)


def _device(ops, options=None, timeout=240):
    return run_accept_device(sys.executable, C_DIR, C_DIR / "unused-relay.db", ops, timeout=timeout, options=options)


@pytest.fixture(scope="module")
def scenario(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("hubboot")
    obs: dict = {}
    client = flask_app.test_client()

    # ── the shop, on the hub (= the main computer) ─────────────────────────────────────────────
    company_id = str(uuid.uuid4())
    email, password = "owner@hubboot.test", "OwnerPW-Hub1"
    reg = registry_conn()
    reg.execute(
        "INSERT INTO users (id, uid, company_id, employee_id, email, password_hash, role, status, "
        "require_password_change) VALUES (?,?,?,?,?,?,?,?,0)",
        (str(uuid.uuid4()), str(uuid.uuid4()), company_id, "EMP-0001", email, hash_password(password), "admin",
         "active"))
    # What `create_admin` records on the FOUNDING computer; the claim route refuses a computer that
    # cannot show it ("JOINED_DEVICE_CANNOT_CLAIM"), and this harness inserts the owner by hand.
    reg.execute("INSERT INTO audit_logs (company_id, action, entity_type, entity_id) VALUES (?, 'ADMIN_CREATED', 'user', 'owner')", (company_id,))
    reg.commit()
    reg.close()
    assert client.post("/api/auth/login", json={"email": email, "password": password}).status_code == 200
    a_inst = LicenseStateRepository(DATA / "database" / "subsystems" / "licensing.db").load().owner_installation_id
    obs.update(company_id=company_id, owner_email=email, owner_password=password, hub_inst=a_inst)

    # the main computer: announce this device, claim the main-computer role (real routes)
    device_context.announce_local_device(
        registry_conn(), company_id, reason="changed", app_version="e2e", retail_schema_version=schema.RETAIL_SCHEMA_VERSION)
    rc = registry_conn()
    rc.commit()
    rc.close()
    claim = client.post("/api/devices/me/claim-admin")
    assert claim.status_code == 200, claim.get_json()

    def post(path, body):
        r = client.post(f"{API}{path}", json=body)
        assert r.status_code == 200, (path, r.status_code, r.get_json())
        return r.get_json()["data"]

    sku1, sku2 = f"HB-1-{uuid.uuid4().hex[:6]}", f"HB-2-{uuid.uuid4().hex[:6]}"
    p1 = post("/products", {"name": "Hub Boot Coffee", "sku": sku1, "cost_price": 1, "sell_price": 4, "tax_rate": 0,
                            "initial_stock": 50})
    post("/products", {"name": "Hub Boot Tea", "sku": sku2, "cost_price": 1, "sell_price": 3, "tax_rate": 0,
                       "initial_stock": 20})
    cust = post("/customers", {"name": "Hub Boot Customer", "phone": "0790000001"})
    sale1 = post("/sales", {"items": [{"product_id": p1["id"], "quantity": 2}], "payment_method": "cash",
                            "customer_id": cust["id"], "idempotency_key": str(uuid.uuid4())})
    series = post("/doc-series", {"doc_type": "sale", "code": "HBS", "label": "Hub Boot Sales"})
    obs.update(skus={sku1, sku2}, customer_id=cust["id"], sale1_id=sale1["id"], series_id=series["id"])

    # ── the hub's own sync: real services over the real site log ──────────────────────────────
    def local():
        return LocalSiteRelayClient(get_conn=get_retail_conn, origin_device_id=a_inst)

    retail_svc = SyncService(lambda: local(), get_retail_conn, local_company_id_from_registry,
                             local_ensure_schema=_ensure_credit_schema, stream_label="retail",
                             local_schema_version_provider=lambda: schema.RETAIL_SCHEMA_VERSION)
    registry_svc = SyncService(lambda: local(), registry_conn, local_company_id_from_registry,
                               handled_entity_types=REGISTRY_SYNC_ENTITY_TYPES, stream_label="registry")

    def tick():
        retail_svc.push_once()
        registry_svc.push_once()
        retail_svc.pull_once()
        registry_svc.pull_once()

    tick()
    conn = get_retail_conn()
    before_b = store.max_event_seq(conn)
    # device B (see module docstring): paired, one event authored, caught up on both streams
    store.pair_device(conn, "device-b", device_public_key="A" * 43 + "=", label="B")
    store.append_events(conn, [{
        "id": str(uuid.uuid4()), "entity_type": "category", "entity_id": str(uuid.uuid4()),
        "event_type": "create", "payload": {"name": "From B"}, "created_at": "2026-10-13T00:00:00+00:00",
    }], origin_device_id="device-b")
    conn.commit()
    k = store.max_event_seq(conn)
    for stream in ("retail", "registry"):
        store.set_device_cursor(conn, "device-b", k, stream)
    conn.commit()
    conn.close()
    tick()                                   # the hub pulls B's event: its cursor reaches k
    # a sale AFTER B's event: it must survive the prune and reach C as an ordinary event
    sale2 = post("/sales", {"items": [{"product_id": p1["id"], "quantity": 1}], "payment_method": "cash",
                            "customer_id": cust["id"], "idempotency_key": str(uuid.uuid4())})
    obs["sale2_id"] = sale2["id"]
    tick()
    conn = get_retail_conn()
    top = store.max_event_seq(conn)
    conn.execute("UPDATE site_forward_cursor SET forwarded_to_seq = ? WHERE id = 1", (top,))   # see docstring
    conn.commit()
    conn.close()
    assert k > before_b >= 1 and top > k

    # ── the prune: real housekeeping, with the SYNC-JOIN 0 floor removed for this test ──────────
    original_floors = app_module._hub_housekeeping_floors
    app_module._hub_housekeeping_floors = app_module._hub_own_consumer_floors
    try:
        sweep = listener.run_housekeeping_sweep(
            get_conn=get_retail_conn, nonce_ttl_seconds=1800,
            consumer_floor_provider=app_module._hub_housekeeping_floors)
    finally:
        app_module._hub_housekeeping_floors = original_floors
    assert sweep is not None and sweep["events_pruned"] > 0, sweep
    conn = get_retail_conn()
    obs["pruned_through"] = store.read_prune_state(conn)[0]
    obs["events_left"] = conn.execute("SELECT COUNT(*) FROM site_sync_events").fetchone()[0]
    conn.close()
    assert obs["pruned_through"] == k, (obs["pruned_through"], k)

    # ── the hub serves the LAN, with the production bootstrap wiring ──────────────────────────
    hub = listener.start_site_relay(
        get_conn=get_retail_conn, host="127.0.0.1", port=0, identity_dir=tmp / "site-relay", housekeeping=False,
        beacon_enabled=False, installation_id=a_inst,
        bootstrap_provider=app_module._hub_bootstrap_provider, bootstrap_board=app_module._hub_bootstrap_board,
        on_bootstrap_requested=app_module._hub_on_bootstrap_requested)
    base_url = f"https://127.0.0.1:{hub.port}"
    obs.update(base_url=base_url)
    try:
        # ── C, a computer bought later: runs once to learn who it is, and is paired at the hub ──
        options = {"hub": {"base_url": base_url, "pin": hub.pin}}
        first = _device([
            {"op": "reg_exec", "sql": "INSERT INTO company_settings (company_id) VALUES (?)", "params": [company_id]},
            {"op": "hub_identity"},
            {"op": "hub_join_record", "url": base_url, "pin": hub.pin, "hub_installation_id": a_inst},
        ], options)
        ident = first[1]
        conn = get_retail_conn()
        store.pair_device(conn, ident["installation_id"], device_public_key=ident["device_public_key"], label="C")
        conn.commit()
        conn.close()
        obs["c_inst"] = ident["installation_id"]

        # C asks. Retail: told to resync -> requests a copy FROM THE HUB -> staged. Registry: skips ahead.
        before = _device([
            {"op": "raw_select", "sql": "SELECT COUNT(*) AS n FROM products"},
            {"op": "pull"},
            {"op": "bootstrap_state"},
            {"op": "raw_select", "sql": "SELECT last_seq FROM sync_cursor WHERE id=1"},
            {"op": "reg_pull"},
            {"op": "reg_select", "sql": "SELECT last_seq FROM sync_cursor WHERE id=1"},
        ], options)
        obs["c_before"] = before
        conn = get_retail_conn()
        obs["hub_cursors_after_request"] = {
            (r["installation_id"], r["stream"]): r["last_seq"] for r in conn.execute(
                "SELECT installation_id, stream, last_seq FROM site_device_cursors")}
        obs["hub_events_when_c_asked"] = store.max_event_seq(conn)
        conn.close()

        # The main computer's tick pushes what the request made it re-announce (users, permissions,
        # who is main, its numbering books) -- AFTER the point C skipped to.
        tick()
        obs["board_after"] = app_module._hub_bootstrap_board.open_requests()

        # C "restarts": init_app() installs the staged copy, THEN it pulls on from the copy's position.
        after_install = _device([
            {"op": "raw_select", "sql": "SELECT sku FROM products"},
            {"op": "raw_select", "sql": "SELECT COUNT(*) AS n FROM doc_series"},
            {"op": "raw_select", "sql": "SELECT last_seq, relay_url FROM sync_cursor WHERE id=1"},
            {"op": "bootstrap_state"},
        ], options)
        obs["c_after_install"] = after_install
        final = _device([
            {"op": "pull"}, {"op": "pull"}, {"op": "reg_pull"}, {"op": "reg_pull"}, {"op": "pull"},
            {"op": "raw_select", "sql": "SELECT sku FROM products"},
            {"op": "raw_select", "sql": "SELECT id FROM customers"},
            {"op": "raw_select", "sql": "SELECT uid FROM sales"},
            {"op": "raw_select", "sql": "SELECT id, code FROM doc_series"},
            {"op": "reg_select", "sql": "SELECT email, role FROM users"},
            {"op": "login", "email": email, "password": password},
            {"op": "bootstrap_state"},
            {"op": "quarantine_rows"},
            {"op": "reg_quarantine_rows"},
        ], options)
        obs["c_final"] = final
        # And once more: nothing is asked for again, nothing is applied twice.
        again = _device([{"op": "pull"}, {"op": "bootstrap_state"},
                         {"op": "raw_select", "sql": "SELECT COUNT(*) AS n FROM sales"}], options)
        obs["c_again"] = again
        obs["hub_sales"] = get_retail_conn().execute("SELECT COUNT(*) FROM sales").fetchone()[0]
        yield obs
    finally:
        hub.server.shutdown()
        hub.server.server_close()


def test_the_hub_really_pruned_its_log_and_says_how_far(scenario):
    assert scenario["pruned_through"] > 0 and scenario["events_left"] > 0


def test_the_late_device_is_told_to_resync_and_applied_nothing_partial(scenario):
    products_before, pull, state, cursor, reg_pull, reg_cursor = scenario["c_before"]
    assert pull["ok"] is False and pull["resync_required"] is True
    assert pull["pruned_through"] == scenario["pruned_through"]
    assert products_before["rows"][0]["n"] == 0          # the 409 let nothing through
    assert cursor["rows"][0]["last_seq"] == 0


def test_it_requested_a_copy_from_the_hub_and_staged_it_for_the_next_start(scenario):
    _, _, state, *_ = scenario["c_before"]
    pending = state["pending"]
    assert pending is not None and pending["as_of"] > 0
    assert pending["relay_url"] == scenario["base_url"]
    assert state["retail_health"]["resync_required"] is True       # stopped, visible, not pulling a partial history


def test_the_hub_set_the_devices_cursor_rows_before_answering(scenario):
    c = scenario["c_inst"]
    cursors = scenario["hub_cursors_after_request"]
    pending = scenario["c_before"][2]["pending"]
    assert cursors[(c, "retail")] == pending["as_of"]
    assert cursors[(c, "registry")] >= pending["as_of"]


def test_the_registry_stream_skipped_ahead_instead_of_stopping(scenario):
    reg_pull, reg_cursor = scenario["c_before"][4], scenario["c_before"][5]
    assert reg_pull == {"ok": True, "_log_warnings": reg_pull["_log_warnings"]}
    assert reg_cursor["rows"][0]["last_seq"] >= scenario["pruned_through"]


def test_the_install_swapped_in_the_shop_and_left_authority_to_come_back_signed(scenario):
    products, doc_series, cursor, state = scenario["c_after_install"]
    assert scenario["skus"] <= {r["sku"] for r in products["rows"]}
    assert doc_series["rows"][0]["n"] == 0                          # AUTHORITY was dropped by the copy
    pending = scenario["c_before"][2]["pending"]
    assert (cursor["rows"][0]["last_seq"], cursor["rows"][0]["relay_url"]) == (pending["as_of"], scenario["base_url"])
    assert state["pending"] is None                                  # consumed
    assert state["marker"]["include_own_once"] == ["retail"]


def test_the_late_device_ends_up_holding_the_shops_products_customers_and_sales(scenario):
    final = scenario["c_final"]
    skus = {r["sku"] for r in final[5]["rows"]}
    customers = {r["id"] for r in final[6]["rows"]}
    sales = {r["uid"] for r in final[7]["rows"]}
    assert scenario["skus"] <= skus
    assert scenario["customer_id"] in customers
    hub_sales = {r[0] for r in get_retail_conn().execute("SELECT uid FROM sales").fetchall()}
    assert len(hub_sales) == 2 and hub_sales == sales, (hub_sales, sales)    # sale 1 from the copy, sale 2 from the tail


def test_the_main_computers_re_announce_brought_the_numbering_book_back_signed(scenario):
    codes = {r["code"] for r in scenario["c_final"][8]["rows"]}
    assert "HBS" in codes


def test_the_owner_can_sign_in_on_the_late_device(scenario):
    final = scenario["c_final"]
    users = {r["email"]: r["role"] for r in final[9]["rows"]}
    assert users.get(scenario["owner_email"]) == "admin"
    assert final[10]["ok"] is True                                   # the login op raises on anything but 200


def test_nothing_was_parked_or_applied_twice(scenario):
    final = scenario["c_final"]
    assert final[12]["rows"] == [] and final[13]["rows"] == []
    sales_after_again = scenario["c_again"][2]["rows"][0]["n"]
    assert sales_after_again == scenario["hub_sales"]


def test_a_second_run_asks_for_nothing_again(scenario):
    again_pull, again_state = scenario["c_again"][0], scenario["c_again"][1]
    assert again_pull["ok"] is True
    assert again_state["pending"] is None
    assert not again_state["marker"].get("include_own_once")        # the one-shot was honoured by the hub and cleared
