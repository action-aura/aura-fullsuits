"""SYNC-JOIN proof: does a computer that joins a shop LATE receive the shop's
existing products, customers and sales -- after Owner's nightly prune has run?

WHY THIS FILE EXISTS. Production runs `flask sync prune-events --apply` every
night (deploy/oracle-free/run_scheduled_ops.sh). It deletes every
owner_sync_events row at or below MIN(last_acked_seq) over the licence's
CURRENTLY ACTIVE devices (app/sync/pruning.py). A computer that has not been
activated yet is not an Installation yet, so it cannot hold anything back. When
it IS activated it pulls from cursor 0 (commercial_runtime/sync/sync_service.py
pull_once), and Owner has no snapshot/bootstrap endpoint -- it can only hand
back the rows still in the table. Nobody had measured what that means for a
shop's third computer. This file measures it, end to end, with nothing faked:

  * Owner is the REAL Owner Flask app on the Owner test database, served over
    real HTTP on 127.0.0.1 (werkzeug, in this process).
  * Each computer is a REAL retail app in its OWN OS process
    (products/retail/tests/_accept_device.py), syncing through the REAL
    SyncRelayClient with a device key that was activated through Owner's REAL
    activation route. Products, the customer and the sale are created through
    the real retail routes, so the events on the wire are the ones the product
    really emits.
  * The prune is the exact production command, `sync prune-events --apply`,
    invoked through Flask's CLI runner against the same app.

THE SCENARIO (both tests share it up to the fork):
  1. Computers A and B are activated on one licence. A founds the shop: two
     products, a customer, a sale to that customer. A pushes.
  2. B pulls A's history (and must hold it -- the harness guard), then B rings
     up its own product and pushes; A pulls that. This is what two tills that
     both trade look like, and it is what moves BOTH Owner cursors past A's
     history (a device that only ever authors events keeps cursor 0 at Owner,
     because pull excludes a device's own rows -- see routes.py pull()).
  3. FORK: prune (the production command) / do not prune (control).
  4. Computer C is activated on the same licence, gets the shop's company id
     (what a real join leaves on it) and pulls until there is nothing more.
  5. C must hold A's products, A's customer and A's sale.

WHAT THIS DOES NOT COVER: the Android client's transport (Kotlin), the LAN hub
(site relay) path -- which has the same shape of pruning in
commercial_runtime/sync/site_relay/pruning.py, see the analysis -- and the
registry stream (users/permissions/devices), which is a second SyncService on
the same Owner licence stream and would lose its pre-join history the same way.

Run (one Owner pytest process at a time -- shared test database):
    OWNER_TEST_DATABASE_URL=... AURA_RETAIL_PYTHON=<repo .venv python> \\
      .venv-owner/Scripts/python.exe -m pytest tests/test_sync_late_join_after_prune_cross_stack.py -q
"""
from __future__ import annotations

import base64
import importlib.util
import json
import os
import sys
import threading
import uuid
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from sqlalchemy import func, select
from werkzeug.serving import make_server

from tests.conftest import build_activation_body, make_device_keypair, make_license, make_staff

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RETAIL_TESTS = _REPO_ROOT / "products" / "retail" / "tests"

# The device processes run the RETAIL app, so they need the retail interpreter.
# AURA_RETAIL_PYTHON wins; otherwise this interpreter (the Owner venv carries the
# same Flask/cryptography/requests the retail app needs).
_DEVICE_PY = os.environ.get("AURA_RETAIL_PYTHON") or sys.executable
_DEVICE_TIMEOUT = 300


def _load_accept_harness():
    """`_accept_harness` imports its sibling `_stock_sync_harness` by bare name,
    so the retail tests directory must be importable for the duration of the
    load. Loaded by path rather than left on sys.path: Owner's own `app` package
    and the retail backend's `app.py` share a name, and this process must only
    ever see Owner's."""
    sys.path.insert(0, str(_RETAIL_TESTS))
    try:
        spec = importlib.util.spec_from_file_location("_accept_harness", _RETAIL_TESTS / "_accept_harness.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(_RETAIL_TESTS))


run_accept_device = _load_accept_harness().run_accept_device


class _OwnerOverHttp:
    """The real Owner app on a real socket. Single-threaded on purpose: one
    request at a time keeps Owner's scoped DB session on one thread, which the
    `app` fixture's per-test TRUNCATE then never races."""

    def __init__(self, app):
        self._server = make_server("127.0.0.1", 0, app, threaded=False)
        self.base_url = f"http://127.0.0.1:{self._server.server_port}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def close(self):
        self._server.shutdown()
        self._thread.join(timeout=10)


class _Computer:
    def __init__(self, tmp_path: Path, name: str, owner: _OwnerOverHttp):
        self.dir = tmp_path / name
        self.owner = owner
        self.installation_id = None
        self.key_b64 = None

    def activate(self, client, full_key: str) -> None:
        """Owner's real activation route with this computer's own Ed25519 key."""
        key = make_device_keypair()
        resp = client.post(
            "/api/licensing/v1/activations",
            data=json.dumps(build_activation_body(key, full_key=full_key, installation_id=str(uuid.uuid4()),
                                                  product_code="AURA_RETAIL")),
            content_type="application/json",
        )
        assert resp.status_code == 200, resp.get_json()
        self.installation_id = resp.get_json()["installation_id"]
        self.key_b64 = base64.b64encode(key.private_bytes(
            encoding=serialization.Encoding.Raw, format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption())).decode("ascii")

    def run(self, actions: list) -> list:
        assert self.installation_id, "computer used before it was activated"
        return run_accept_device(
            _DEVICE_PY, self.dir, self.dir / "_unused_relay.db", actions, timeout=_DEVICE_TIMEOUT,
            options={"owner_relay": {"base_url": self.owner.base_url, "installation_id": self.installation_id,
                                     "device_key_b64": self.key_b64}},
        )

    def select(self, sql: str, params=None) -> list:
        [r] = self.run([{"op": "raw_select", "sql": sql, "params": params or []}])
        return r["rows"]

    def pull_until_quiet(self, rounds: int = 4) -> None:
        """Owner hands back at most 500 rows per pull; a few pulls in one process
        drain anything this scenario can produce."""
        self.run([{"op": "pull"} for _ in range(rounds)])


def _event_count(app, license_id) -> int:
    with app.app_context():
        from app.extensions import db_session
        from app.models.sync import SyncEvent

        return db_session.execute(
            select(func.count()).select_from(SyncEvent).where(SyncEvent.license_id == license_id)
        ).scalar_one()


@pytest.fixture
def shop(app, client, seeded, signing_key, tmp_path, monkeypatch):
    """Steps 1-2 of the module docstring. Returns everything the fork needs."""
    for var in ("AURA_OWNER_LICENSING_URL", "AURA_SYNC_RELAY_URL", "OWNER_LICENSING_BASE_URL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("AURA_SITE_RELAY_ENABLED", "0")
    monkeypatch.setenv("AURA_BACKUP_SCHEDULER", "0")

    actor_id = make_staff(app, "sync-late-join@example.com")
    license_id, full_key = make_license(app, actor_id, device_limit=3, product_code="AURA_RETAIL")
    owner = _OwnerOverHttp(app)
    try:
        a, b, c = (_Computer(tmp_path, n, owner) for n in ("a", "b", "c"))
        a.activate(client, full_key)
        b.activate(client, full_key)

        # -- A founds the shop and trades -----------------------------------
        sku_1, sku_2 = f"LATE-A1-{uuid.uuid4().hex[:6]}", f"LATE-A2-{uuid.uuid4().hex[:6]}"
        [created] = a.run([{"op": "create_shop", "price": 10.0, "initial_stock": 50}])
        login = {"op": "login", "email": created["email"], "password": created["password"]}
        _, p2, cust = a.run([
            login,
            {"op": "create_product", "name": "Late Join Second Product", "sku": sku_2, "price": 4.0,
             "initial_stock": 20},
            {"op": "http", "method": "POST", "path": "/api/sub/retail/customers",
             "json": {"name": "Late Join Customer", "phone": "0790000000"}},
        ])
        assert p2["status_code"] == 200, p2
        assert cust["status_code"] == 200, cust
        customer_id = cust["body"]["data"]["id"]
        _, sale = a.run([login, {"op": "http", "method": "POST", "path": "/api/sub/retail/sales", "json": {
            "items": [{"product_id": created["product_id"], "quantity": 2}],
            "payment_method": "cash", "customer_id": customer_id, "idempotency_key": str(uuid.uuid4())}}])
        assert sale["status_code"] == 200, sale
        a.run([{"op": "push"}])

        a_products = {r["sku"]: r["id"] for r in a.select("SELECT sku, id FROM products")}
        a_customers = {r["name"]: r["id"] for r in a.select("SELECT name, id FROM customers")}
        a_sales = {r["uid"] for r in a.select("SELECT uid FROM sales")}
        assert sku_2 in a_products and len(a_products) == 2, a_products
        assert a_customers.get("Late Join Customer"), a_customers
        assert len(a_sales) == 1, a_sales

        # -- B joins NOW, receives the history, and trades too --------------
        [boot] = b.run([{"op": "bootstrap"}])
        b.pull_until_quiet()
        # HARNESS GUARD: the transport really carries history to a computer that
        # is present when it is pushed. If B does not hold A's rows here, every
        # later assertion about C would be measuring a broken harness.
        b_skus = {r["sku"] for r in b.select("SELECT sku FROM products")}
        assert set(a_products) <= b_skus, (a_products, b_skus)
        b_sales = {r["uid"] for r in b.select("SELECT uid FROM sales")}
        assert a_sales <= b_sales, (a_sales, b_sales)

        sku_b = f"LATE-B1-{uuid.uuid4().hex[:6]}"
        [_, pb] = b.run([{"op": "login", "email": boot["email"], "password": boot["password"]},
                         {"op": "create_product", "name": "B Product", "sku": sku_b, "price": 3.0}])
        assert pb["status_code"] == 200, pb
        b.run([{"op": "push"}])
        a.pull_until_quiet()
        b.pull_until_quiet()

        yield {
            "app": app, "client": client, "full_key": full_key, "license_id": license_id,
            "a": a, "b": b, "c": c, "company_id": created["company_id"],
            "a_products": a_products, "a_customers": a_customers, "a_sales": a_sales, "sku_b": sku_b,
        }
    finally:
        owner.close()


def _prune_exactly_as_production_does(app) -> dict:
    """deploy/oracle-free/run_scheduled_ops.sh: `flask --app app:create_app sync
    prune-events --apply`, on the process whose OWNER_SCHEDULER_ROLE is owner."""
    app.config["SCHEDULER_ROLE"] = "owner"
    result = app.test_cli_runner().invoke(args=["sync", "prune-events", "--apply"])
    assert result.exit_code == 0, result.output
    # The command echoes one indented JSON report first (app/cli.py prune_events_cmd).
    report, _ = json.JSONDecoder().raw_decode(result.output.strip())
    return report


def _join_c_and_read(shop) -> dict:
    """Step 4: activate C (only now -- a computer bought later), give it the
    shop's company id, pull until quiet, and read what it holds."""
    c = shop["c"]
    c.activate(shop["client"], shop["full_key"])
    c.run([{"op": "bootstrap"}])
    c.run([{"op": "reg_exec", "sql": "UPDATE company_settings SET company_id=?", "params": [shop["company_id"]]},
           {"op": "reg_exec", "sql": "UPDATE users SET company_id=?", "params": [shop["company_id"]]}])
    c.pull_until_quiet()
    return {
        "skus": {r["sku"] for r in c.select("SELECT sku FROM products")},
        "customers": {r["id"] for r in c.select("SELECT id FROM customers")},
        "sales": {r["uid"] for r in c.select("SELECT uid FROM sales")},
    }


def test_control_late_joiner_without_prune_receives_the_shops_history(shop):
    """CONTROL. No prune: C, activated after the fact, receives everything A and
    B created before it existed. This is what proves the harness CAN deliver
    history to a late joiner -- so a failure in the pruned test below is the
    prune, not the plumbing."""
    events_before = _event_count(shop["app"], shop["license_id"])
    assert events_before > 0
    held = _join_c_and_read(shop)
    assert set(shop["a_products"]) <= held["skus"], (shop["a_products"], held["skus"])
    assert shop["sku_b"] in held["skus"], held["skus"]
    assert set(shop["a_customers"].values()) <= held["customers"], (shop["a_customers"], held["customers"])
    assert shop["a_sales"] <= held["sales"], (shop["a_sales"], held["sales"])


@pytest.mark.xfail(
    strict=True, raises=AssertionError,
    reason=(
        "SYNC-JOIN gap (measured 2026-10-13): Owner's nightly `sync prune-events --apply` deletes every "
        "event all CURRENTLY active devices have acked; a computer activated afterwards pulls from 0 and "
        "Owner has no snapshot to give it, so it never receives the shop's earlier products, customers "
        "or sales. See the module docstring; remove this mark when a bootstrap exists."
    ),
)
def test_late_joiner_after_production_prune_receives_the_shops_history(shop):
    """THE PROOF. Same shop, but the production prune runs before C is bought.

    The prune's own effect is checked with pytest.fail (NOT assert) so that a
    prune that deleted nothing turns this test RED instead of being swallowed as
    the expected xfail: the xfail must only ever stand for "C did not get the
    history", never for "the scenario did not happen"."""
    app, license_id = shop["app"], shop["license_id"]
    before = _event_count(app, license_id)
    report = _prune_exactly_as_production_does(app)
    after = _event_count(app, license_id)
    if not (report.get("dry_run") is False and after < before):
        # NOTE FOR WHOEVER FIXES THE GAP: if the fix is "the prune keeps a
        # bootstrap floor", this guard is the line that goes red -- replace it
        # with a check that the floor was kept, and remove the xfail mark.
        pytest.fail(f"the production prune deleted nothing ({before} -> {after}, report {report}); "
                    "the scenario did not happen, so nothing below would mean anything")

    held = _join_c_and_read(shop)
    # B's own product event has a seq above every Owner cursor (A acked up to it
    # only by RECEIVING it; B's cursor sits at A's last event), so it survives
    # the prune and C must get it. That proves C's sync works at all here, so
    # the misses below are the prune's doing, not a dead C.
    if shop["sku_b"] not in held["skus"]:
        pytest.fail(f"C did not even receive the un-pruned tail ({held}); C's sync is broken, not the history")

    missing = {
        "products": sorted(set(shop["a_products"]) - held["skus"]),
        "customers": sorted(set(shop["a_customers"].values()) - held["customers"]),
        "sales": sorted(shop["a_sales"] - held["sales"]),
    }
    assert not any(missing.values()), (
        f"late joiner is missing the shop's history: {missing} "
        f"(owner_sync_events for the licence: {before} -> {after} after the prune)")
