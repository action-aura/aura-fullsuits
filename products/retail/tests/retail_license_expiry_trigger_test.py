"""
Aura Retail -- P0-2 real-trigger expiry regression test.

AUDIT P0-2 (highest-value new test called out in the audit): every existing
capability-guard test in this suite (retail_capability_guard_test.py, and
commercial_runtime/licensing_contracts/tests/test_flask_guard.py) proves the
GUARD's behavior by writing current_state directly into licensing.db and
then hitting a route -- useful for testing the guard in isolation, but it
never actually exercises the mechanism that is supposed to put a real
install into that state in the first place. Before P0-2, nothing on Windows
ever did that automatically at all (see products/retail/backend/app.py's
init_app() -- the periodic LicenseCheckInScheduler this test exercises).

This test proves the REAL trigger path end to end, with no shortcuts:
  1. A license is activated with a real, correctly-signed assertion carrying
     a short-grace offline policy (built directly, the same way
     commercial_runtime/licensing_contracts/tests/test_routes.py and
     test_checkin_scheduler.py construct a signed test assertion/policy --
     the only direct DB write here seeds the LEGITIMATE starting state,
     ACTIVE_ONLINE, never the RESTRICTED state under test).
  2. Trusted time is advanced artificially by monkeypatching time.monotonic
     (commercial_runtime/licensing_contracts/trusted_time.py's source of
     "now" -- never a real sleep), past that policy's offline_grace_seconds
     + retry_interval_seconds.
  3. Exactly one tick of the REAL scheduler app.py wires up at boot
     (app._license_checkin_scheduler, the actual production object, not a
     reimplementation) is run.
  4. Only THEN is a capability-guarded route hit, and only then does it
     return 403 -- proving elapsed time -> scheduler tick -> guard denies,
     the actual mechanism, not a simulation of its effect.

Run:
    pytest products/retail/tests/retail_license_expiry_trigger_test.py -v
"""
import base64
import json
import os
import shutil
import sys
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_expiry_trigger_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)
os.environ.pop("AURA_RETAIL_DEMO_MODE", None)
# Deliberately empty (default) -- OWNER_LICENSING_BASE_URL is not configured
# in this test environment, matching every other test file's bootstrap; this
# test never calls run_once() (which would attempt a live Owner HTTP call)
# for exactly that reason -- see test_expiry below.

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402
from commercial_runtime.licensing_contracts.canonical import canonicalize_bytes  # noqa: E402
from commercial_runtime.licensing_contracts.state_repository import (  # noqa: E402
    LICENSING_SCHEMA_VERSION,
    LicenseStateRecord,
    LicenseStateRepository,
)
from commercial_runtime.licensing_contracts import trusted_time  # noqa: E402

INSTALLATION_ID = "expiry-trigger-installation"
NOW = datetime.now(timezone.utc)


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


def _db_path() -> Path:
    return DATA / "database" / "subsystems" / "licensing.db"


def _b64_pub(private_key) -> str:
    raw = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )
    return base64.b64encode(raw).decode("ascii")


def _short_grace_policy() -> dict:
    # Deliberately short so the test can advance trusted time by a small,
    # fast-to-reason-about amount rather than needing to simulate weeks.
    # offline_grace_seconds=60 + retry_interval_seconds=10 -> RESTRICTED
    # once elapsed_offline >= 70s (see policy_evaluator.evaluate()).
    return {
        "check_in_interval_seconds": 5,
        "retry_interval_seconds": 10,
        "offline_grace_seconds": 60,
        "warning_start_seconds": 30,
        "hard_expiry_behavior": "RESTRICT_COMMERCIAL_FEATURES_PRESERVE_DATA",
        "clock_rollback_tolerance_seconds": 300,
        "assertion_refresh_threshold_seconds": 86400,
    }


def _signed_envelope(owner_key, key_id: str, device_key_fingerprint: str) -> dict:
    payload = {
        "assertion_id": "expiry-trigger-assertion",
        "issuer": "aura-owner",
        "product_code": "AURA_RETAIL",
        "license_public_id": "lic-expiry-trigger",
        "installation_public_id": INSTALLATION_ID,
        "platform": "WINDOWS",
        "app_version_policy": "1.0.0-rc.5",
        "release_channel": "rc",
        "issued_at": NOW.isoformat(),
        "not_before": (NOW - timedelta(minutes=5)).isoformat(),
        "expires_at": (NOW + timedelta(days=1)).isoformat(),
        "license_status": "ACTIVE",
        "installation_status": "ACTIVE",
        "subscription_status": "ACTIVE",
        "allowed_device_count": 2,
        "device_key_fingerprint": device_key_fingerprint,
        "entitlements": {},
        "offline_policy": _short_grace_policy(),
        "contract_version": "v1",
    }
    sig = owner_key.sign(canonicalize_bytes(payload))
    return {
        "payload": payload,
        "signing_key_id": key_id,
        "algorithm": "ed25519",
        "assertion_version": 1,
        "signature": base64.b64encode(sig).decode("ascii"),
    }


def _make_admin_client():
    email = f'expiry-trigger-{uuid.uuid4().hex[:8]}@test.local'
    password = 'ExpiryTriggerPW1'
    company_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, require_password_change) "
        "VALUES (?,?,?,?,?,?,?,0)",
        (user_id, company_id, 'EMP-0001', email, hash_password(password), 'admin', 'active'),
    )
    conn.commit()
    conn.close()
    c = app.test_client()
    r = c.post('/api/auth/login', json={'email': email, 'password': password})
    assert r.status_code == 200, r.get_json()
    return c


def test_elapsed_trusted_time_through_real_scheduler_tick_triggers_real_403(monkeypatch):
    import time as time_module

    trusted_time._ANCHOR_CACHE.clear()

    # -- Step 0: a real, logged-in session (state is still ACTIVE_ONLINE at
    # this point -- login itself is never license-gated). ------------------
    client = _make_admin_client()

    # -- Step 1: activate with a REAL signed assertion. The device key is
    # generated through the app's own real scheduler singleton's signer (the
    # exact object app.py's init_app() wired at boot), never a second,
    # disconnected identity -- so the fingerprint embedded in the signed
    # assertion below is the one verify_assertion() will actually check
    # against later. -----------------------------------------------------
    scheduler = _app_module._license_checkin_scheduler
    assert scheduler is not None, "P0-2 wiring: Windows must construct a boot-time scheduler"
    device_meta = scheduler._signer.generate_new_key()

    owner_key = Ed25519PrivateKey.generate()
    # Bootstrap the SAME OwnerTrustStore object instance the scheduler
    # already holds (captured once at app-import time, via build_licensing_
    # context() inside make_checkin_scheduler()) -- not a second, freshly
    # constructed instance pointed at the same file path. OwnerTrustStore
    # keeps its trusted-key set in memory and only re-reads the file at its
    # own __init__ time; a second instance writing to the same path would
    # never be seen by the scheduler's already-in-memory (and, at this
    # point, still-empty) copy.
    scheduler._trust_store.bootstrap_from_anchor(
        {"keys": [{"key_id": "owner-1", "public_key": _b64_pub(owner_key), "algorithm": "ed25519"}]}
    )
    envelope = _signed_envelope(owner_key, "owner-1", device_meta.public_key_fingerprint)

    # The ONLY direct DB write in this test: seeds the LEGITIMATE starting
    # condition (a genuinely activated, currently-valid license) -- never
    # the RESTRICTED state under test. current_state here is ACTIVE_ONLINE,
    # exactly what a real successful activation would have persisted; only
    # the scheduler tick below is ever allowed to change it from here.
    state_repo = LicenseStateRepository(_db_path())
    state_repo.save(
        LicenseStateRecord(
            licensing_schema_version=LICENSING_SCHEMA_VERSION,
            product_code="AURA_RETAIL",
            platform="WINDOWS",
            current_state="ACTIVE_ONLINE",
            owner_installation_id=INSTALLATION_ID,
            device_public_key_fingerprint=device_meta.public_key_fingerprint,
            assertion_envelope_json=json.dumps(envelope),
            assertion_id="expiry-trigger-assertion",
            assertion_issued_at=NOW.isoformat(),
            assertion_not_before=(NOW - timedelta(minutes=5)).isoformat(),
            assertion_expires_at=(NOW + timedelta(days=1)).isoformat(),
            trusted_time_anchor_server_time=NOW.isoformat(),
            last_successful_checkin_at=NOW.isoformat(),
            license_status="ACTIVE",
            installation_status="ACTIVE",
            subscription_status="ACTIVE",
            offline_policy_json=json.dumps(_short_grace_policy()),
            entitlements_json=json.dumps({}),
        )
    )

    # Sanity: the guarded route works NORMALLY right now, before any time
    # has elapsed -- proves the eventual 403 below is caused by what this
    # test actually does, not by some unrelated pre-existing block.
    ok_resp = client.post('/api/sub/retail/products', json={'name': 'Before', 'sku': f'SKU-{uuid.uuid4().hex[:6]}'})
    assert ok_resp.status_code == 200, ok_resp.get_json()

    # -- Step 2: advance TRUSTED time artificially (monkeypatch time.
    # monotonic -- trusted_time.py's own source of "now" -- never a real
    # sleep). Pin the anchor first at a known monotonic reading, exactly
    # like a real successful check-in would (cache_fresh_anchor is what
    # _persist_fresh_assertion calls in production), then move the fake
    # clock forward past offline_grace_seconds(60) + retry_interval_
    # seconds(10) = 70s. --------------------------------------------------
    fake_monotonic = [10_000.0]
    monkeypatch.setattr(time_module, "monotonic", lambda: fake_monotonic[0])
    trusted_time.cache_fresh_anchor(INSTALLATION_ID, NOW)
    fake_monotonic[0] = 10_000.0 + 3600.0  # +1 hour, well past the 70s threshold

    # -- Step 3: run exactly ONE tick of the REAL production scheduler --
    # the same object app.py's init_app() constructed and .start()-ed at
    # boot, not a fresh reimplementation. reevaluate_only() is the
    # network-free re-evaluation path (no OWNER_LICENSING_BASE_URL is
    # configured in this test environment, matching every other test file
    # here) -- the identical evaluate_policy()/verify_assertion() pipeline
    # run_once()'s own failed-check-in fallback and the periodic .start()
    # timer both funnel through (see checkin_scheduler.LicenseCheckInScheduler
    # ._reevaluate(), fixed in the same audit to fail closed rather than
    # crash on a corrupt/rolled-back clock -- P0-3, landed before this).
    new_state = scheduler.reevaluate_only(checkin_ok=False)

    assert new_state.value == "RESTRICTED"
    assert state_repo.load().current_state == "RESTRICTED"  # persisted by the real transition, not by this test

    # -- Step 4: only NOW does the guard deny -- the real trigger path. --
    resp = client.post('/api/sub/retail/products', json={'name': 'After', 'sku': f'SKU-{uuid.uuid4().hex[:6]}'})
    assert resp.status_code == 403
    assert resp.get_json()["reason_code"] == "LICENSE_INACTIVE"

    trusted_time._ANCHOR_CACHE.clear()
