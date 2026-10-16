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

SYNC-BOOT PHASE 1 made Owner TELL a computer that is behind its pruned range
(409 RESYNC_REQUIRED, `test_late_joiner_after_prune_is_told_to_resync`) instead of
handing it a silent, gap-free-looking partial stream. SYNC-BOOT PHASE 3 is what the
computer does about it, and `test_late_joiner_after_production_prune_receives_the_shops_history`
is the ACCEPTANCE GATE for the whole programme (no xfail any more):

  * every computer is a signed identity (AURA_ACCEPT_SIGNED: a DPAPI device key and an
    Owner-signed lease), A is the shop's MAIN computer, and all of them sync BOTH
    streams (retail + registry) through the one real Owner;
  * B and C JOIN through the real activation-time seed (`join_seed` =
    `company_rebind.rebind_company_id_after_activation`), with nothing patched in;
  * C is told to resync on both streams, asks Owner's mailbox for a copy, A (the main)
    claims it, builds it from its own database, encrypts it to C's single-use key and
    uploads it; A also re-announces the shop's authority (staff, till numbers) signed;
    C downloads, verifies and stages the copy, restarts (a fresh process: the staged
    copy is installed before any database opens), pulls on from the copy's position;
  * C must then hold A's products, customer and sale AND sign A's owner in.

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
                                     "device_key_b64": self.key_b64},
                     # The shipped shape: a cursor per stream, both streams over the one Owner, and
                     # the bootstrap hooks production wires (see _accept_device.py, Phase 3 notes).
                     "stream_labels": True, "registry_owner_relay": True, "owner_bootstrap": True},
        )

    def select(self, sql: str, params=None) -> list:
        [r] = self.run([{"op": "raw_select", "sql": sql, "params": params or []}])
        return r["rows"]

    def pull_results(self, rounds: int = 4) -> list:
        """The raw results of `rounds` retail pulls. A pull Owner refuses with
        RESYNC_REQUIRED comes back as {"resync_required": True, "pruned_through": N}
        (the harness no longer lets it escape as an exception)."""
        return self.run([{"op": "pull"} for _ in range(rounds)])

    def pull_until_quiet(self, rounds: int = 4) -> None:
        """Both streams, a few rounds in one process: Owner hands back at most 500 rows
        per pull, and registry events (staff, devices) must land before anything that
        needs them. A computer that is PRESENT when history is pushed must never be
        told to resync -- that would be the harness lying about the control."""
        results = self.run([{"op": op} for _ in range(rounds) for op in ("reg_pull", "pull")])
        refused = [r for r in results if r.get("resync_required")]
        assert not refused, f"a computer that was never behind was told to resync: {refused}"


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
    # Every computer of the shop signs and verifies for real, under ONE licence id -- which is
    # also the shop's company id once a computer has activated (company_rebind).
    public_licence = f"LIC-LATEJOIN-{uuid.uuid4().hex[:8]}"
    monkeypatch.setenv("AURA_ACCEPT_SIGNED", "1")
    monkeypatch.setenv("AURA_ACCEPT_SIGNED_LICENCE", public_licence)

    actor_id = make_staff(app, "sync-late-join@example.com")
    license_id, full_key = make_license(app, actor_id, device_limit=3, product_code="AURA_RETAIL")
    # The mailbox's encrypted blobs live in this test's own directory, never in the repo tree.
    app.config["SYNC_BOOTSTRAP_DIRECTORY"] = str(tmp_path / "owner-mailbox")
    owner = _OwnerOverHttp(app)
    try:
        a, b, c = (_Computer(tmp_path, n, owner) for n in ("a", "b", "c"))
        a.activate(client, full_key)
        b.activate(client, full_key)

        # -- A founds the shop, becomes its MAIN computer, and trades ---------
        sku_1, sku_2 = f"LATE-A1-{uuid.uuid4().hex[:6]}", f"LATE-A2-{uuid.uuid4().hex[:6]}"
        [created] = a.run([{"op": "create_shop", "price": 10.0, "initial_stock": 50, "company_id": public_licence,
                            "admin_created": True, "with_uid": True}])
        login = {"op": "login", "email": created["email"], "password": created["password"]}
        _, announced, claimed = a.run([login, {"op": "announce", "reason": "changed"}, {"op": "claim_main"}])
        assert claimed["status_code"] == 200, claimed
        # The founding computer's users reach other computers ONLY as the main's signed re-announce
        # (create_admin writes the row directly; there is no `user` event until then). In production
        # this is the heartbeat tick; the harness calls the same seam.
        [reannounce] = a.run([{"op": "app_announce", "reason": "heartbeat"}])
        assert reannounce["marker"] is not None, "the main computer did not re-announce its authority state"
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
        a.run([{"op": "push"}, {"op": "reg_push"}])

        a_products = {r["sku"]: r["id"] for r in a.select("SELECT sku, id FROM products")}
        a_customers = {r["name"]: r["id"] for r in a.select("SELECT name, id FROM customers")}
        a_sales = {r["uid"] for r in a.select("SELECT uid FROM sales")}
        assert sku_2 in a_products and len(a_products) == 2, a_products
        assert a_customers.get("Late Join Customer"), a_customers
        assert len(a_sales) == 1, a_sales

        # -- B joins NOW (the real join seed), receives the history, and trades too ---
        [seed] = b.run([{"op": "join_seed"}])
        assert seed["seeded_company_settings"] is True and seed["status"] != "failed", seed
        b.pull_until_quiet()
        # HARNESS GUARD: the transport really carries history to a computer that
        # is present when it is pushed. If B does not hold A's rows here, every
        # later assertion about C would be measuring a broken harness.
        b_skus = {r["sku"] for r in b.select("SELECT sku FROM products")}
        assert set(a_products) <= b_skus, (a_products, b_skus)
        b_sales = {r["uid"] for r in b.select("SELECT uid FROM sales")}
        assert a_sales <= b_sales, (a_sales, b_sales)

        sku_b = f"LATE-B1-{uuid.uuid4().hex[:6]}"
        # B signs in with A's owner account: it arrived over the registry stream, signed by A.
        [_, pb] = b.run([{"op": "login", "email": created["email"], "password": created["password"]},
                         {"op": "create_product", "name": "B Product", "sku": sku_b, "price": 3.0}])
        assert pb["status_code"] == 200, pb
        b.run([{"op": "push"}, {"op": "reg_push"}])
        a.pull_until_quiet()
        b.pull_until_quiet()

        yield {
            "app": app, "client": client, "full_key": full_key, "license_id": license_id,
            "a": a, "b": b, "c": c, "company_id": created["company_id"], "owner_login": login,
            "public_licence": public_licence,
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


def _join_c(shop) -> "_Computer":
    """Step 4, before any pull: activate C (only now -- a computer bought later) and run the REAL
    activation-time join seed. Nothing is patched in: the shop's company id reaches C the way it
    reaches a customer's new computer, through the licence its lease names."""
    c = shop["c"]
    c.activate(shop["client"], shop["full_key"])
    [seed] = c.run([{"op": "join_seed"}])
    assert seed["seeded_company_settings"] is True and seed["status"] != "failed", seed
    [settings] = c.run([{"op": "reg_select", "sql": "SELECT company_id FROM company_settings"}])
    assert settings["rows"] == [{"company_id": shop["public_licence"]}] and shop["public_licence"] == shop["company_id"], (
        settings, shop["company_id"])
    return c


def _bootstrap_rows(shop, c) -> list:
    """Owner's mailbox rows for C, with whether their encrypted bytes are still on Owner's disk."""
    with shop["app"].app_context():
        from app.extensions import db_session
        from app.models.sync import SyncBootstrapRequest

        db_session.expire_all()
        base = Path(shop["app"].config["SYNC_BOOTSTRAP_DIRECTORY"])
        rows = db_session.query(SyncBootstrapRequest).filter(
            SyncBootstrapRequest.requester_installation_id == uuid.UUID(c.installation_id)
        ).order_by(SyncBootstrapRequest.created_at).all()
        return [{"stream": r.stream, "status": r.status,
                 "producer": str(r.producer_installation_id) if r.producer_installation_id else None,
                 "blob_present": (base / str(r.id)).exists()} for r in rows]


def _the_mailbox_hands_c_the_shop(shop, c) -> dict:
    """C was told RESYNC_REQUIRED on both streams. Run the shipped recovery with each computer
    doing exactly what production code does, one process at a time:

      C   asks Owner's mailbox for a copy                       (OwnerRequester)
      A   (the MAIN computer) sees the request in its pull, claims it, builds the copy from its own
          database, encrypts it to C's key, uploads it          (app._bootstrap_on_mailbox_requests)
      A   sees the READY copy / the registry request in its next pull, re-announces the shop's
          authority signed, tells Owner, and pushes it         (force_main_reannounce)
      C   downloads, verifies, decrypts and STAGES the copy    (installer.stage)
      C   restarts: a fresh process installs it before any database opens.
    Returns what was observed along the way."""
    a = shop["a"]
    obs: dict = {}
    obs["c_requested"] = c.run([{"op": "mailbox_step"}])[0]
    assert obs["c_requested"]["outcome"] == "requested", obs["c_requested"]
    a.run([{"op": "pull"}])
    # Owner's own record is the evidence that A really produced: READY, claimed by A, bytes on disk.
    rows = _bootstrap_rows(shop, c)
    assert [(r["stream"], r["status"], r["producer"]) for r in rows if r["stream"] == "retail"] == [
        ("retail", "READY", a.installation_id)], rows
    a.run([{"op": "pull"}, {"op": "push"}, {"op": "reg_push"}])
    obs["c_staged"] = c.run([{"op": "mailbox_step"}])[0]
    assert obs["c_staged"]["outcome"] == "staged", obs["c_staged"]
    # ...and the ack deleted the bytes the moment C had its own staged copy.
    obs["rows_after"] = _bootstrap_rows(shop, c)
    assert [r["status"] for r in obs["rows_after"] if r["stream"] == "retail"] == ["DELIVERED"]         or [r["status"] for r in obs["rows_after"] if r["stream"] == "retail"] == ["FULFILLED"], obs["rows_after"]
    assert not any(r["blob_present"] for r in obs["rows_after"]), obs["rows_after"]
    obs["c_before_restart"] = c.run([{"op": "mailbox_state"}])[0]
    return obs


def _join_c_and_read(shop) -> dict:
    """Step 4: join C, let it sync, and read what it holds -- including whether the shop's owner
    can sign in on it. If Owner tells C it is behind, the recovery above runs first."""
    c = _join_c(shop)
    first = c.run([{"op": "pull"}, {"op": "reg_pull"}])
    mailbox = None
    if first[0].get("resync_required"):
        assert first[1].get("ok") is True, first[1]      # the registry stream skipped ahead, did not stop
        mailbox = _the_mailbox_hands_c_the_shop(shop, c)
    # (after a copy: this is a NEW process = the restart; before it, a plain catch-up)
    c.pull_until_quiet(rounds=3)
    login = c.run([shop["owner_login"]])[0]
    return {
        "skus": {r["sku"] for r in c.select("SELECT sku FROM products")},
        "customers": {r["id"] for r in c.select("SELECT id FROM customers")},
        "sales": {r["uid"] for r in c.select("SELECT uid FROM sales")},
        "owner_login_ok": login.get("ok") is True,
        "mailbox": mailbox,
        "quarantine": c.run([{"op": "quarantine_rows"}, {"op": "reg_quarantine_rows"}]),
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
    assert held["owner_login_ok"] is True
    assert held["mailbox"] is None, "no prune -> nobody should have needed a bootstrap"


def test_late_joiner_after_prune_is_told_to_resync(shop):
    """SYNC-BOOT Phase 1. The same shop and the same production prune as the xfail
    below -- but what is asserted is what Owner now DOES: it tells C its cursor is
    below the pruned range instead of handing back the un-pruned tail and letting C
    believe that is the shop.

    Both halves matter and are checked separately:
      * C is refused (RESYNC_REQUIRED, with Owner's own pruned_through number),
        every time it asks again -- it does not slip through on a later round;
      * C applied NOTHING and its cursor did not move: a refusal that still let a
        partial batch through would be the silent partial history again.
    MUTATION: delete the `since < pruned_through` branch in owner/app/sync/routes.py
    pull() and C's pulls succeed with the tail only -> this test goes red."""
    app, license_id = shop["app"], shop["license_id"]
    before = _event_count(app, license_id)
    report = _prune_exactly_as_production_does(app)
    after = _event_count(app, license_id)
    if not (report.get("dry_run") is False and after < before):
        pytest.fail(f"the production prune deleted nothing ({before} -> {after}, report {report}); "
                    "the scenario did not happen, so nothing below would mean anything")
    with app.app_context():
        from app.sync.routes import _license_pruned_through
        pruned_through = _license_pruned_through(license_id)
    assert pruned_through > 0, "the prune ran but Owner did not record how far (pruned_through is still 0)"

    c = _join_c(shop)
    results = c.pull_results(rounds=3)
    assert all(r.get("resync_required") is True for r in results), results
    assert all(r.get("pruned_through") == pruned_through for r in results), (results, pruned_through)

    # nothing partial was applied, nothing was acknowledged
    assert c.select("SELECT sku FROM products") == [], "C applied rows from a pull Owner had refused"
    assert c.select("SELECT COUNT(*) AS n FROM sales")[0]["n"] == 0
    assert c.select("SELECT last_seq FROM sync_cursor WHERE id=1")[0]["last_seq"] == 0
    # ...and Owner recorded no acknowledgement for C either (a 409 writes no cursor)
    with app.app_context():
        from app.extensions import db_session
        from app.models.sync import SyncDeviceCursor
        rows = db_session.query(SyncDeviceCursor).filter(
            SyncDeviceCursor.installation_id == uuid.UUID(c.installation_id)).all()
        assert rows == [], [(r.stream, r.last_acked_seq) for r in rows]


def test_late_joiner_after_production_prune_receives_the_shops_history(shop):
    """THE ACCEPTANCE GATE (SYNC-JOIN gap, measured 2026-10-13; closed by SYNC-BOOT). Same shop,
    but the production prune runs before C is bought. This was a strict xfail until the bootstrap
    existed; it now has to PASS: C holds A's products, customer and sale, B's product, and the
    shop's owner signs in on it.

    The prune's own effect is checked with pytest.fail (NOT assert) so that a prune that deleted
    nothing turns this test RED instead of passing vacuously: "C got the history" only means
    something if the history had really been pruned away first."""
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

    pruned_floor = _pruned_through(app, license_id)
    assert pruned_floor > 0
    held = _join_c_and_read(shop)
    # The recovery really ran (not a lucky replay): C was told to resync, A produced, C staged.
    assert held["mailbox"] is not None, "C was never told to resync, so no bootstrap was exercised"
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
    assert held["owner_login_ok"], "C holds the data but the shop's owner cannot sign in on it"
    # Nothing was parked: a copy that needed events the prune deleted would show up here.
    assert held["quarantine"][0]["rows"] == [] and held["quarantine"][1]["rows"] == [], held["quarantine"]


def _pruned_through(app, license_id) -> int:
    with app.app_context():
        from app.sync.routes import _license_pruned_through
        return _license_pruned_through(license_id)
