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

Track S + registry v9 additions (P2S crew Y). All are OPT-IN through an
optional fifth argv: a JSON object of options. A caller that passes four
arguments gets exactly the behaviour every earlier acceptance test was written
against.

  * `code_root` -- run this device on a DIFFERENT checkout of the product (an
    export of an older commit) while still using THIS file and this tests
    directory's `FileRelay`. That is how one test process drives a real OLD
    build and a real NEW build against each other: the point of a mixed-build
    check is that the old side is old CODE, not a flag on new code. Every op an
    old tree does not support fails there (an ImportError or a 404), which a
    test sees as a failure, never as a silent pass.
  * `registry_relay` -- path of a second FileRelay for the REGISTRY stream. The
    real app runs two SyncService instances (retail.db and registry.db); the
    harness used to build only the retail one, so user and device events had no
    way to leave a harness device. With this option a registry-stream service
    is built exactly as `app._build_windows_sync_services` builds it (same
    handled_entity_types, no schema-version provider: registry.db carries its
    own version, and stamping retail's integer onto it would quarantine good
    rows).
  * `arm_provider` -- give the RETAIL service the same
    `local_schema_version_provider` the real app gives it, so the skew guard is
    live. Off by default because the harness never armed it and older tests
    were written without it.

SYNC-BOOT Phase 1 additions (also OPT-IN, owner_relay runs only):

  * `stream_labels` -- build the retail service with stream_label='retail' (and
    the registry service with 'registry'), as the shipped app does, so Owner
    keeps one cursor per stream for this computer. Off by default: the SYNC-JOIN
    proof was written against the legacy single-cursor shape.
  * `registry_owner_relay` -- run the REGISTRY service over the same real Owner
    relay client instead of a FileRelay, so both streams of one computer talk to
    one Owner, which is the whole shape the per-stream cursors exist for.
  * `pull` / `reg_pull` no longer let Owner's RESYNC_REQUIRED escape as an
    exception: the op returns {"ok": False, "resync_required": True,
    "pruned_through": N} and the CALLER decides. Silently treating it as "pulled
    nothing" is exactly the partial-history failure the answer exists to stop.

Run as a bare subprocess, same contract as its ancestors:
    python _accept_device.py <app_data_dir> <relay_db_path> <actions_path> <output_path> [<options_json>]
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


def _pull_result(pull_once) -> dict:
    """Runs one pull; Owner's RESYNC_REQUIRED becomes an explicit result instead
    of an exception (see the module docstring). Anything else still raises."""
    # Duck-typed on reason_code, no import: this file also runs on OLD checkouts
    # (`code_root`) whose relay_client has neither RESYNC_REQUIRED nor .details.
    try:
        pull_once()
    except Exception as exc:
        if getattr(exc, "reason_code", None) != "RESYNC_REQUIRED":
            raise
        details = getattr(exc, "details", None) or {}
        return {"ok": False, "resync_required": True, "pruned_through": details.get("pruned_through")}
    return {"ok": True}


def _provision_signed_identity(app_data_dir: str) -> None:
    """Give THIS computer what a real activated till has, so the production code
    path (`authority.default_authority_env`, reading app data) signs and verifies
    for real: a Windows DPAPI device key, an Owner-signed lease naming this
    computer's key and installation, and a trust store that trusts the (shared,
    deterministic) test Owner key -- the same key on every computer of the shop,
    so each trusts the others' leases. The licence id is the shop's, the same on
    every computer of a test: `AURA_ACCEPT_SIGNED_LICENCE` (the test file sets it to
    the licence it already uses), else a fixed default.

    Runs at the start of EVERY process because `seed_active_license` just above
    overwrites the single licensing row (and so the lease) each time."""
    import base64
    import dataclasses
    import hashlib
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from commercial_runtime.identity import device_context
    from commercial_runtime.licensing_contracts.device_identity import (
        WindowsDpapiDeviceIdentityProvider, fingerprint_of)
    from commercial_runtime.licensing_contracts.state_repository import LicenseStateRepository
    from commercial_runtime.licensing_contracts.trust_store import OwnerTrustStore
    from commercial_runtime.sync.tests._signing import make_lease

    app = Path(app_data_dir)
    licence = os.environ.get("AURA_ACCEPT_SIGNED_LICENCE", "LIC-ACCEPT-SIGNED")
    owner_key = Ed25519PrivateKey.from_private_bytes(hashlib.sha256(b"aura-accept-test-owner").digest())
    owner_pub = owner_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)
    trust_path = app / "licensing" / "trust_store.json"
    if not trust_path.exists():
        trust_path.parent.mkdir(parents=True, exist_ok=True)
        OwnerTrustStore(trust_path).bootstrap_from_anchor({"keys": [{
            "key_id": "owner-1", "public_key": base64.b64encode(owner_pub).decode("ascii"),
            "algorithm": "ed25519"}]})
    provider = WindowsDpapiDeviceIdentityProvider(app / "licensing")
    if not provider.has_key():
        provider.generate_new_key()
    fingerprint = fingerprint_of(base64.b64decode(provider.get_public_key_b64()))
    device_id = device_context.local_device_uuid()
    installation = f"inst-{device_id}"
    # AUDIT-033 fix round (verifier V finding #1): a real Owner pins each
    # computer's own registry id into its lease (registry_pin.py). The harness
    # models that pin by attesting this app-data's own local_device_uuid, so
    # an insider process can only ever be attested as ITSELF.
    lease = make_lease(owner_key, licence=licence, inst=installation, fingerprint=fingerprint,
                       registry_device_id=device_id)
    repo = LicenseStateRepository(app / "database" / "subsystems" / "licensing.db")
    record = repo.load()
    repo.save(dataclasses.replace(
        record, owner_installation_id=installation, assertion_envelope_json=json.dumps(lease)))


def main() -> None:
    app_data_dir, relay_db_path, actions_path, output_path = sys.argv[1:5]
    options = json.loads(sys.argv[5]) if len(sys.argv) > 5 and sys.argv[5] else {}

    tests_dir = Path(__file__).resolve().parent
    if options.get("code_root"):
        # An older checkout. `tests_dir` stays THIS directory (FileRelay lives
        # here); only the product code is swapped.
        suite_root = Path(options["code_root"]).resolve()
        product_dir = suite_root / "products" / "retail"
    else:
        product_dir = tests_dir.parent
        suite_root = product_dir.parent.parent
    backend_dir = product_dir / "backend"
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

    # AUDIT-033 / S-AUTH: opt-in REAL signing identity for this computer (see
    # `_provision_signed_identity`). Off unless the test process asked for it, and
    # never for an old-build device (`code_root`): that build has no signing code.
    # Deliberately AFTER `init_app()`: a licence present at boot would trigger the
    # identity company rebind (company id -> licence id), which these harness shops
    # (random company ids, no real activation) were never built to survive; the
    # lease is only needed once sync runs.
    if os.environ.get("AURA_ACCEPT_SIGNED") and not options.get("code_root"):
        _provision_signed_identity(app_data_dir)

    import api.retail_api as retail_api_module
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

    if options.get("owner_relay"):
        # SYNC-JOIN proof (owner/tests/test_sync_late_join_after_prune_cross_stack.py):
        # the REAL transport -- SyncRelayClient, signed exactly as the shipped
        # client signs -- against a REAL Owner relay the test serves over HTTP on
        # 127.0.0.1. The device key and installation id are the ones the test
        # activated through Owner's real activation route; nothing on the wire is
        # faked. `relay_db_path` is unused on this path.
        import base64 as _b64
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey as _Ed
        from commercial_runtime.sync.relay_client import SyncRelayClient

        _owner_relay = options["owner_relay"]
        _device_key = _Ed.from_private_bytes(_b64.b64decode(_owner_relay["device_key_b64"]))

        class _RawKeySigner:
            def sign(self, canonical_bytes: bytes) -> str:
                return _b64.b64encode(_device_key.sign(canonical_bytes)).decode("ascii")

        relay = SyncRelayClient(_owner_relay["base_url"], _RawKeySigner(),
                                _owner_relay["installation_id"], max_retries=0)
    else:
        relay = FileRelay(relay_db_path)
    service_kwargs = {}
    if options.get("stream_labels"):
        service_kwargs["stream_label"] = "retail"
    if options.get("arm_provider"):
        from database.schema import RETAIL_SCHEMA_VERSION as _RSV
        service_kwargs["local_schema_version_provider"] = lambda: _RSV
    service = SyncService(
        client_factory=lambda: relay,
        get_conn=get_retail_conn,
        local_company_id_provider=local_company_id_from_registry,
        local_ensure_schema=_ensure_credit_schema,
        **service_kwargs,
    )

    # The REGISTRY stream (users, permissions, and from registry v9 devices and
    # the main-computer assignment) -- built the way the real app builds it,
    # see the module docstring. Absent unless the caller asks for it.
    registry_service = None
    if options.get("registry_relay") or options.get("registry_owner_relay"):
        from commercial_runtime.sync.sync_service import REGISTRY_SYNC_ENTITY_TYPES
        if options.get("registry_owner_relay"):
            assert options.get("owner_relay"), "registry_owner_relay needs owner_relay"
            registry_relay = relay
        else:
            registry_relay = FileRelay(options["registry_relay"])
        registry_kwargs = {"stream_label": "registry"} if options.get("stream_labels") else {}
        registry_service = SyncService(
            client_factory=lambda: registry_relay,
            get_conn=registry_conn,
            local_company_id_provider=local_company_id_from_registry,
            handled_entity_types=REGISTRY_SYNC_ENTITY_TYPES,
            **registry_kwargs,
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
            results.append(_pull_result(service.pull_once))

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

        elif op == "set_terminal_id":
            # Aseel-parity wave A-PAR (schema v34): `local_terminal_id()`
            # (database/schema.py, imported into retail_api as a bare
            # name) resolves a REAL device identity file that a fresh
            # process/AURA_APP_DATA dir typically has not established yet
            # -- AUDIT-032D's whole point. Monkeypatched here exactly like
            # retail_drawer_terminal_scope_test.py's own `at_terminal`
            # helper, rather than relying on some real action to create
            # the identity file as a side effect, so this device's
            # "which till am I" answer is deterministic and under this
            # test's own control across a real process boundary.
            retail_api_module.local_terminal_id = (lambda tid=action["terminal_id"]: tid)
            results.append({"ok": True})

        elif op == "create_doc_series":
            # Aseel-parity wave A-PAR (schema v34, retail-hardware-
            # viewports): per-document-type numbering series. Added here
            # rather than in a second device script, matching this file's
            # own module docstring convention ("every existing op ...
            # reproduced here UNCHANGED; this file only ADDS what [a] pass
            # specifically needs") -- `create_po`'s optional `branch_id`
            # is the precedent for extending an existing op set this way.
            payload = {"doc_type": action["doc_type"], "code": action["code"],
                       "label": action.get("label", action["code"])}
            for k in ("branch_uid", "pad_width", "start_no"):
                if action.get(k) is not None:
                    payload[k] = action[k]
            r = client.post(f"{API}/doc-series", json=payload)
            results.append({"status_code": r.status_code, "body": r.get_json(),
                             "series_id": (r.get_json().get("data") or {}).get("id") if r.status_code == 200 else None})

        elif op == "claim_doc_series":
            r = client.post(f"{API}/doc-series/{action['series_id']}/allocator-claim")
            results.append({"status_code": r.status_code, "body": r.get_json()})

        elif op == "list_doc_series":
            qs = f"?doc_type={action['doc_type']}" if action.get("doc_type") else ""
            r = client.get(f"{API}/doc-series{qs}")
            results.append({"status_code": r.status_code, "body": r.get_json(),
                             "data": (r.get_json().get("data") or []) if r.status_code == 200 else []})

        elif op == "quarantine_rows":
            conn = get_retail_conn()
            try:
                rows = conn.execute(
                    "SELECT entity_id, entity_type, event_type, reason, detail FROM sync_apply_quarantine "
                    "ORDER BY quarantined_at"
                ).fetchall()
            finally:
                conn.close()
            results.append({"rows": [dict(r) for r in rows]})

        elif op == "sync_conflict_rows":
            conn = get_retail_conn()
            try:
                rows = conn.execute(
                    "SELECT entity_id, entity_type, event_type, local_row_version, incoming_row_version "
                    "FROM sync_conflicts ORDER BY detected_at_utc"
                ).fetchall()
            finally:
                conn.close()
            results.append({"rows": [dict(r) for r in rows]})

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

        elif op == "numbering_get":
            # Phase 1b fix round: the readable-numbers switch is per COMPUTER,
            # so the two-device regression test must drive it over the real
            # route on each device (GET /numbering), not poke settings rows.
            qs = "&".join(f"{k}={action[k]}" for k in ("branch_uid", "till_number") if action.get(k) is not None)
            r = client.get(f"{API}/numbering" + (f"?{qs}" if qs else ""))
            results.append({"status_code": r.status_code, "body": r.get_json()})

        elif op == "numbering_set":
            payload = {k: action[k] for k in ("branch_uid", "branch_code", "readable_enabled", "till_number") if k in action}
            r = client.post(f"{API}/numbering", json=payload)
            results.append({"status_code": r.status_code, "body": r.get_json()})

        elif op == "allocate_till":
            # v42: the MAIN computer hands a till number to a computer
            # (POST /tills/allocations). Body keys are passed through only when
            # given, so a test can also send {all: true} or omit the number.
            payload = {k: action[k] for k in ("terminal_uid", "till_number", "branch_uid", "all") if k in action}
            r = client.post(f"{API}/tills/allocations", json=payload)
            results.append({"status_code": r.status_code, "body": r.get_json(silent=True)})

        elif op == "release_till":
            r = client.delete(f"{API}/tills/allocations", json={"terminal_uid": action["terminal_uid"]})
            results.append({"status_code": r.status_code, "body": r.get_json(silent=True)})

        elif op == "set_readable_available":
            # Round 3: readable numbers are UNAVAILABLE in the shipped build
            # (retail_api.READABLE_NUMBERS_AVAILABLE=False) until the device
            # registry syncs (Track S). The route therefore refuses to switch a
            # computer on. Tests that must still exercise the UNDERLYING window
            # (what Track S will have to close) flip the flag inside this ONE
            # device process, exactly as Track S's own code will one day flip it
            # for real. Never used by a test that claims to test the shipped build.
            retail_api_module.READABLE_NUMBERS_AVAILABLE = bool(action["value"])
            results.append({"ok": True, "value": retail_api_module.READABLE_NUMBERS_AVAILABLE})

        elif op == "raw_exec":
            # Direct write for tests that must plant a state no route can now
            # create (e.g. a computer left switched ON by an older build).
            conn = get_retail_conn()
            try:
                conn.execute(action["sql"], action.get("params", []))
                conn.commit()
            finally:
                conn.close()
            results.append({"ok": True})

        # ── Track S + registry v9 ops (P2S crew Y) ─────────────────────────
        elif op == "code_identity":
            # Which checkout is this process REALLY running? A mixed-build test
            # is worthless if the "old" device silently imported the new code
            # (an editable install, a stray sys.path entry), so the test asks.
            import commercial_runtime.identity.registry_db as _rdb
            import commercial_runtime.sync.sync_service as _ss
            import database.schema as _schema
            results.append({
                "suite_root": str(suite_root),
                "sync_service_file": str(Path(_ss.__file__).resolve()),
                "registry_schema_version": _rdb.REGISTRY_SCHEMA_VERSION,
                "retail_schema_version": _schema.RETAIL_SCHEMA_VERSION,
                "registry_entity_types": sorted(_ss.REGISTRY_SYNC_ENTITY_TYPES),
            })

        elif op == "activate_shared_licence":
            # Two devices of ONE shop hold the same Owner licence. The harness's
            # own `seed_active_license` cannot pin the id (and test_support.py is
            # not ours to edit), so save a record whose signed envelope names it
            # -- the same place `license_public_id` is read from at runtime.
            from commercial_runtime.licensing_contracts.state_machine import LicenseState
            from commercial_runtime.licensing_contracts.state_repository import (
                LICENSING_SCHEMA_VERSION, LicenseStateRecord, LicenseStateRepository,
            )
            if os.environ.get("AURA_ACCEPT_SIGNED") and not options.get("code_root"):
                # The real signing identity carries the licence id inside a REAL
                # Owner-signed lease; record the shop's id and re-issue it.
                if action["license_public_id"] != os.environ.get("AURA_ACCEPT_SIGNED_LICENCE", "LIC-ACCEPT-SIGNED"):
                    raise RuntimeError(
                        "activate_shared_licence names a different licence than the one every computer of "
                        "this test signs under (AURA_ACCEPT_SIGNED_LICENCE)")
                _provision_signed_identity(app_data_dir)
            else:
                repo = LicenseStateRepository(Path(app_data_dir) / "database" / "subsystems" / "licensing.db")
                repo.save(LicenseStateRecord(
                    licensing_schema_version=LICENSING_SCHEMA_VERSION,
                    product_code="AURA_RETAIL", platform=action.get("platform", "WINDOWS"),
                    current_state=LicenseState.ACTIVE_ONLINE.value,
                    owner_installation_id=action.get("installation_id", f"inst-{uuid.uuid4().hex[:8]}"),
                    license_status="ACTIVE", installation_status="ACTIVE", subscription_status="ACTIVE",
                    entitlements_json=json.dumps(action.get("entitlements") or {}),
                    assertion_envelope_json=json.dumps({"payload": {"license_public_id": action["license_public_id"]}}),
                ))
            results.append({"ok": True, "license_public_id": action["license_public_id"]})

        elif op == "http":
            # Generic route call, for routes a named op does not exist for yet.
            # `path` is absolute (starts with /api/...).
            method = action.get("method", "GET").lower()
            kwargs = {"json": action["json"]} if "json" in action else {}
            r = getattr(client, method)(action["path"], **kwargs)
            results.append({"status_code": r.status_code, "body": r.get_json(silent=True)})

        elif op == "sync_review":
            r = client.get(f"{API}/sync-review")
            results.append({"status_code": r.status_code, "body": r.get_json(silent=True)})

        elif op == "devices_list":
            r = client.get("/api/devices")
            results.append({"status_code": r.status_code, "body": r.get_json(silent=True)})

        elif op == "device_me":
            r = client.get("/api/devices/me")
            results.append({"status_code": r.status_code, "body": r.get_json(silent=True)})

        elif op == "claim_main":
            r = client.post("/api/devices/me/claim-admin")
            results.append({"status_code": r.status_code, "body": r.get_json(silent=True)})

        elif op == "transfer_main":
            r = client.post(f"/api/devices/{action['device_id']}/admin")
            results.append({"status_code": r.status_code, "body": r.get_json(silent=True)})

        elif op == "take_over":
            r = client.post("/api/devices/me/take-over",
                            json={"password": action["password"], "confirm": action.get("confirm", "TAKE OVER")})
            results.append({"status_code": r.status_code, "body": r.get_json(silent=True)})

        elif op == "device_label":
            r = client.post(f"/api/devices/{action['device_id']}/label", json={"label": action["label"]})
            results.append({"status_code": r.status_code, "body": r.get_json(silent=True)})

        elif op == "device_revoke":
            r = client.post(f"/api/devices/{action['device_id']}/revoke")
            results.append({"status_code": r.status_code, "body": r.get_json(silent=True)})

        elif op == "announce":
            # This device announces itself the way the boot seam does. The real
            # boot seam (`app._announce_boot_device`) already ran once inside
            # `init_app()` -- BEFORE this run created a company, so in a first
            # run it correctly did nothing. A test that needs the real seam
            # starts a SECOND process on the same app_data_dir.
            from commercial_runtime.identity.device_context import announce_local_device
            from database.schema import RETAIL_SCHEMA_VERSION as _RSV2
            cid = local_company_id_from_registry()
            rconn = registry_conn()
            try:
                out = announce_local_device(
                    rconn, cid, reason=action.get("reason", "changed"),
                    app_version=action.get("app_version", "harness-build"),
                    retail_schema_version=action.get("retail_schema_version", _RSV2),
                    device_label=action.get("device_label"),
                )
                rconn.commit()
            finally:
                rconn.close()
            results.append({"announced": out["announced"], "device": dict(out["device"])})

        elif op == "app_announce":
            # The REAL app seam (`app._announce_device`): announce + self-bind +
            # the signed authority re-announce (AUDIT-033 B2b). The harness
            # provisions a signing identity only AFTER `init_app()`, so the boot
            # call inside `init_app()` always runs signer-less here; a test that
            # models "the shop upgrades, then the computer ticks" calls this op
            # once the identity exists. Returns the device-local marker so the
            # test can see whether the re-announce ran.
            _app_module._announce_device(action.get("reason", "heartbeat"))
            _marker = Path(app_data_dir) / "device" / "authority_reannounce.json"
            results.append({"marker": json.loads(_marker.read_text(encoding="utf-8"))
                            if _marker.exists() else None})

        elif op in ("reg_push", "reg_pull"):
            if registry_service is None:
                raise RuntimeError("registry stream not configured: pass options.registry_relay")
            if op == "reg_pull":
                results.append(_pull_result(registry_service.pull_once))
            else:
                registry_service.push_once()
                results.append({"ok": True})

        elif op == "reg_apply_raw":
            # Registry-stream twin of `apply_result_raw`: a caller-supplied pull
            # result, applied as-is, callable repeatedly with the SAME events to
            # prove replay convergence across a process boundary.
            if registry_service is None:
                raise RuntimeError("registry stream not configured: pass options.registry_relay")
            rconn = registry_conn()
            try:
                registry_service.apply_pull_result(rconn, {"events": action["events"], "cursor": action["cursor"]})
                rconn.commit()
            finally:
                rconn.close()
            results.append({"ok": True})

        elif op == "reg_select":
            rconn = registry_conn()
            try:
                rows = rconn.execute(action["sql"], action.get("params", [])).fetchall()
            finally:
                rconn.close()
            results.append({"rows": [dict(r) for r in rows]})

        elif op == "reg_exec":
            # Plant a state no route can create (an OLD build's row shape).
            rconn = registry_conn()
            try:
                rconn.execute(action["sql"], action.get("params", []))
                rconn.commit()
            finally:
                rconn.close()
            results.append({"ok": True})

        elif op == "reg_outbox":
            rconn = registry_conn()
            try:
                rows = rconn.execute(
                    "SELECT entity_type, entity_id, event_type, payload FROM sync_outbox ORDER BY rowid"
                ).fetchall()
            finally:
                rconn.close()
            results.append({"rows": [{**dict(r), "payload": json.loads(r["payload"])} for r in rows]})

        elif op == "reg_quarantine_rows":
            rconn = registry_conn()
            try:
                has = rconn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sync_apply_quarantine'"
                ).fetchone()
                rows = rconn.execute(
                    "SELECT entity_id, entity_type, event_type, reason, detail FROM sync_apply_quarantine "
                    "ORDER BY quarantined_at, rowid"
                ).fetchall() if has else []
            finally:
                rconn.close()
            results.append({"rows": [dict(r) for r in rows]})

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
