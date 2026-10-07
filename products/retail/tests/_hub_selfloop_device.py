"""One REAL Aura Retail till in its own OS process, driven over stdin/stdout
by retail_site_relay_self_loop_test.py (REHEARSAL-1007 B1).

Why a long-lived process per till, not the one-shot `_stock_sync_device.py`
shape: the defect needs two tills ALIVE AT ONCE -- B joins A's real,
listening LAN hub over real pinned TLS, then the hub role moves from A to B.
And why a process at all: `database/schema.py` resolves its paths once, at
import, from AURA_APP_DATA (see `_stock_sync_harness.py`'s module docstring,
"the AURA_APP_DATA trap"), so two tills cannot share one interpreter.

LAB SAFETY (the owner's own till may be a LAN hub on 5443 on this laptop):
  * the hub port is whatever the test passes (>= 5300, never 5443) and the
    listener binds 127.0.0.1 only;
  * `beacon.BeaconBroadcaster` is a no-op stub, so nothing is ever broadcast
    on UDP 45455 -- the owner's till can never hear these test hubs;
  * `SiteRelayCoordinator` is a capturing fake, so no election runs and no
    UDP socket is bound; the test drives the REAL `start_relay` /
    `stop_relay` / `attempt_join` seams app.py builds;
  * autojoin's beacon LISTENER is replaced by a file read, so a join only
    ever targets the hub pointer the test wrote;
  * `SyncService.start` is a no-op: every push/pull/forward runs only when
    the test says so.

Protocol: one JSON object per stdin line ({"op": ..., ...}); one reply line
per command on stdout, prefixed "@@" (anything else on stdout is ignored).

argv[1] = path to a JSON config:
  data_dir, hub_port, site_relay_enabled (null = automatic, "0" = kill
  switch), persisted_relay_url, owner_priv_b64, license_public_id,
  installation_id, preseed (optional: {"cursor_relay_url", "cursor_last_seq",
  "site_rows": [{"origin": ...}], "forwarded_to_seq"}).
"""
import base64
import json
import os
import sqlite3
import sys
import traceback
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

CFG = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / "backend"
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA = Path(CFG["data_dir"])
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
for _var in ("AURA_SYNC_RELAY_URL", "AURA_OWNER_LICENSING_URL", "AURA_DEV",
             "AURA_SITE_RELAY_ENABLED"):
    os.environ.pop(_var, None)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA),
                  AURA_SITE_RELAY_PORT=str(CFG["hub_port"]), AURA_SITE_RELAY_BIND_HOST="127.0.0.1")
if CFG.get("site_relay_enabled") is not None:
    os.environ["AURA_SITE_RELAY_ENABLED"] = CFG["site_relay_enabled"]

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402

from commercial_runtime.licensing_contracts.canonical import canonicalize_bytes  # noqa: E402
from commercial_runtime.licensing_contracts.device_identity import (  # noqa: E402
    WindowsDpapiDeviceIdentityProvider, fingerprint_of)
from commercial_runtime.licensing_contracts.state_machine import LicenseState  # noqa: E402
from commercial_runtime.licensing_contracts.state_repository import (  # noqa: E402
    LicenseStateRecord, LicenseStateRepository)
from commercial_runtime.licensing_contracts.trust_store import OwnerTrustStore  # noqa: E402

LICENSING_DIR = DATA / "licensing"
LICENSING_DB = DATA / "database" / "subsystems" / "licensing.db"
INSTALLATION_ID = CFG["installation_id"]


def _out(obj):
    sys.stdout.write("@@" + json.dumps(obj, default=str) + "\n")
    sys.stdout.flush()


def _activate_offline():
    """An Owner-signed licence for THIS till's real device key, minted with
    the test's own Owner key, plus a trust store naming that key -- what a
    real activation leaves behind, with no Owner server involved."""
    owner_key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(CFG["owner_priv_b64"]))
    owner_pub = base64.b64encode(owner_key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode("ascii")
    LICENSING_DIR.mkdir(parents=True, exist_ok=True)
    OwnerTrustStore(LICENSING_DIR / "trust_store.json").bootstrap_from_anchor(
        {"keys": [{"key_id": "owner-test-1", "public_key": owner_pub, "algorithm": "ed25519"}]})

    device = WindowsDpapiDeviceIdentityProvider(LICENSING_DIR)
    if not device.has_key():
        device.generate_new_key()
    fingerprint = fingerprint_of(base64.b64decode(device.get_public_key_b64()))
    now = datetime.now(timezone.utc)
    payload = {
        "assertion_id": "a-" + uuid.uuid4().hex[:8], "issuer": "aura-owner",
        "product_code": "AURA_RETAIL", "license_public_id": CFG["license_public_id"],
        "installation_public_id": INSTALLATION_ID, "platform": "WINDOWS",
        "app_version_policy": "1.0.0", "release_channel": "stable",
        "issued_at": now.isoformat(), "not_before": (now - timedelta(minutes=5)).isoformat(),
        "expires_at": (now + timedelta(days=1)).isoformat(),
        "license_status": "ACTIVE", "installation_status": "ACTIVE", "subscription_status": "ACTIVE",
        "allowed_device_count": 5, "device_key_fingerprint": fingerprint, "entitlements": {},
        "offline_policy": {
            "check_in_interval_seconds": 86400, "retry_interval_seconds": 3600,
            "offline_grace_seconds": 1209600, "warning_start_seconds": 864000,
            "hard_expiry_behavior": "RESTRICT_COMMERCIAL_FEATURES_PRESERVE_DATA",
            "clock_rollback_tolerance_seconds": 300, "assertion_refresh_threshold_seconds": 86400,
        },
        "contract_version": "v1",
    }
    envelope = {"payload": payload, "signing_key_id": "owner-test-1", "algorithm": "ed25519",
                "signature": base64.b64encode(owner_key.sign(canonicalize_bytes(payload))).decode("ascii")}
    LicenseStateRepository(LICENSING_DB).save(LicenseStateRecord(
        licensing_schema_version=2, product_code="AURA_RETAIL", platform="WINDOWS",
        current_state=LicenseState.ACTIVE_ONLINE.value, owner_installation_id=INSTALLATION_ID,
        device_public_key_fingerprint=fingerprint, assertion_envelope_json=json.dumps(envelope),
        sync_relay_base_url=CFG.get("persisted_relay_url"),
    ))


def _preseed():
    """A PRIOR process's state, written before `import app`: a sync cursor
    (relay + position) and site-log rows -- how a till that was a hub, or
    that the old autojoin damaged, looks on disk at its next boot."""
    pre = CFG.get("preseed")
    if not pre:
        return
    from database import schema as _schema

    _schema.init_retail()
    conn = sqlite3.connect(_schema._get_path("retail"))
    try:
        for row in pre.get("site_rows", []):
            conn.execute(
                "INSERT INTO site_sync_events (id, entity_type, entity_id, event_type, payload, "
                "created_at, origin_device_id) VALUES (?,?,?,?,?,?,?)",
                (str(uuid.uuid4()), "category", str(uuid.uuid4()), "create", "{}",
                 "2026-10-07T00:00:00+00:00",
                 INSTALLATION_ID if row["origin"] == "__self__" else row["origin"]))
        if pre.get("cursor_relay_url"):
            conn.execute("INSERT OR IGNORE INTO sync_cursor (id, last_seq) VALUES (1, 0)")
            conn.execute("UPDATE sync_cursor SET relay_url=?, last_seq=? WHERE id=1",
                         (pre["cursor_relay_url"], pre.get("cursor_last_seq", 0)))
        if "forwarded_to_seq" in pre:
            conn.execute("UPDATE site_forward_cursor SET forwarded_to_seq=? WHERE id=1",
                         (pre["forwarded_to_seq"],))
        conn.commit()
    finally:
        conn.close()


_activate_offline()
_preseed()

# ── Lab-safety stubs, all installed BEFORE `import app` ─────────────────────
from commercial_runtime.sync.site_relay import autojoin as _autojoin  # noqa: E402
from commercial_runtime.sync.site_relay import beacon as _beacon  # noqa: E402
from commercial_runtime.sync.site_relay import coordinator as _coordinator  # noqa: E402
from commercial_runtime.sync import sync_service as _sync_service_module  # noqa: E402

_captured = {}


class _CapturingCoordinator:
    def __init__(self, **kwargs):
        _captured.update(kwargs)

    def start(self):
        pass

    def stop(self):
        pass


class _NoOpBeaconBroadcaster:
    def __init__(self, **kwargs):
        pass

    def start(self):
        pass

    def stop(self):
        pass


_POINTER_FILE = DATA / "_beacon_pointer.json"


def _file_beacon_listener(timeout_seconds):
    try:
        return json.loads(_POINTER_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


_coordinator.SiteRelayCoordinator = _CapturingCoordinator
_beacon.BeaconBroadcaster = _NoOpBeaconBroadcaster
_autojoin._default_beacon_listener = _file_beacon_listener
_sync_service_module.SyncService.start = lambda self, *a, **kw: None

import app as _app  # noqa: E402

_app.SITE_FORWARDER_INTERVAL_SECONDS = 3600.0
_app.init_app()


def _retail_conn():
    from database.schema import get_retail_conn
    return get_retail_conn()


def _licence_relay_url():
    record = LicenseStateRepository(LICENSING_DB).load()
    return record.sync_relay_base_url if record else None


def _op(cmd):
    op = cmd["op"]
    if op == "state":
        selector = _app._sync_relay_selector
        client = None
        if _app._sync_service is not None:
            try:
                client = getattr(_app._sync_service._client_factory(), "base_url", None)
            except Exception as exc:  # noqa: BLE001
                client = "ERROR:" + (getattr(exc, "reason_code", None) or type(exc).__name__)
        loop = _app._site_forwarder_loop
        return {
            "installation_id": INSTALLATION_ID,
            "licence_relay_url": _licence_relay_url(),
            "selector_local": bool(selector and selector.is_local()),
            "own_sync_client_base_url": client,
            "forwarder_running": loop is not None,
            "forwarder_pull_down": (loop.pull_down_enabled if loop is not None else None),
            "joined_hub": _app._joined_hub_store().load(),
            "hub_bound": _app._site_relay_server is not None,
        }
    if op == "become_hub":
        handle = _captured["start_relay"]()
        return {"url": f"https://127.0.0.1:{handle.port}", "spki_pin": handle.pin,
                "installation_id": INSTALLATION_ID}
    if op == "stand_down":
        _captured["stop_relay"](_app._site_relay_server)
        return {"ok": True}
    if op == "set_beacon":
        _POINTER_FILE.write_text(json.dumps(cmd["pointer"]), encoding="utf-8")
        return {"ok": True}
    if op == "attempt_join":
        _captured["attempt_join"]()
        return {"licence_relay_url": _licence_relay_url(), "joined_hub": _app._joined_hub_store().load()}
    if op == "forwarder_tick":
        return _app._site_forwarder_loop.tick()
    if op == "emit_category":
        cat_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        conn = _retail_conn()
        try:
            conn.execute(
                "INSERT INTO sync_outbox (id, entity_type, entity_id, event_type, payload, created_at) "
                "VALUES (?,?,?,?,?,?)",
                (str(uuid.uuid4()), "category", cat_id, "create",
                 json.dumps({"id": cat_id, "name": cmd["name"], "description": "",
                             "row_version": 1, "updated_at_utc": now}), now))
            conn.commit()
        finally:
            conn.close()
        return {"id": cat_id}
    if op == "sync_run_once":
        out = {}
        for name in ("_sync_service", "_registry_sync_service"):
            svc = getattr(_app, name)
            try:
                svc.run_once()
                out[name] = "ok"
            except Exception as exc:  # noqa: BLE001
                out[name] = getattr(exc, "reason_code", None) or repr(exc)
        return out
    if op == "seed_company":
        from commercial_runtime.identity import registry_db
        conn = registry_db.get_conn()
        try:
            conn.execute("INSERT OR IGNORE INTO company_settings (id, company_id) VALUES (?, ?)",
                         (str(uuid.uuid4()), cmd["company_id"]))
            conn.commit()
        finally:
            conn.close()
        return {"ok": True}
    if op == "category_names":
        conn = _retail_conn()
        try:
            return [r[0] for r in conn.execute("SELECT name FROM categories ORDER BY name")]
        finally:
            conn.close()
    if op == "health":
        return _sync_service_module.get_active_health()
    raise ValueError(f"unknown op {op!r}")


_out({"ready": True, "installation_id": INSTALLATION_ID})
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    cmd = json.loads(line)
    if cmd.get("op") == "quit":
        _out({"bye": True})
        break
    try:
        _out({"ok": True, "result": _op(cmd)})
    except Exception as exc:  # noqa: BLE001
        _out({"ok": False, "error": repr(exc), "trace": traceback.format_exc()})

handle = _app._site_relay_server
if handle is not None:
    try:
        handle.server.shutdown()
        handle.server.server_close()
    except Exception:
        pass
