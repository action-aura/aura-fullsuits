"""Forge a signed trial lease into a retail AURA_APP_DATA -- helper for retail_trial_lock_e2e.py.

Run with the REPO VENV python (needs `cryptography`). Throwaway test keys only; never a production key.

HOW THE LEASE IS FORGED (and why it is honest):
  * A throwaway Ed25519 "owner" key is generated HERE (never a production key) and its public half is
    written into the install's own licensing/trust_store.json via OwnerTrustStore.bootstrap_from_anchor,
    exactly the call the app itself makes from the bundled anchor.
  * The device key is created by the app's own WindowsDpapiDeviceIdentityProvider so the signed
    device_key_fingerprint is the REAL fingerprint of this machine's key for this app-data dir.
  * The envelope is signed with canonicalize_bytes + Ed25519 -- the same bytes Owner signs -- and
    persisted through LicenseStateRepository.save as the stored assertion, with the state still
    ACTIVE_ONLINE. NOTHING writes TRIAL_ENDED: the app must reach it by evaluating this lease.

usage: retail_trial_lock_seed.py <app_data_dir> <term_end YYYY-MM-DD> [PILOT|COMPLETED|EXPIRED|PAST_DUE|ACTIVE] [commercial_grace_end ISO]

  PAST_DUE + a commercial_grace_end is what Owner signs inside the 7-day payment grace (grace_end ahead = still
  selling, grace_end behind = the till must lock as UNPAID_LOCKED); EXPIRED is what it signs after the grace.
"""
import base64
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

from commercial_runtime.licensing_contracts.canonical import canonicalize_bytes  # noqa: E402
from commercial_runtime.licensing_contracts.device_identity import WindowsDpapiDeviceIdentityProvider  # noqa: E402
from commercial_runtime.licensing_contracts.state_repository import (  # noqa: E402
    LICENSING_SCHEMA_VERSION, LicenseStateRecord, LicenseStateRepository)
from commercial_runtime.licensing_contracts.trust_store import OwnerTrustStore  # noqa: E402

app_data = Path(sys.argv[1])
term_end = sys.argv[2]
status = sys.argv[3] if len(sys.argv) > 3 else "PILOT"
grace_end = sys.argv[4] if len(sys.argv) > 4 else None

licensing_dir = app_data / "licensing"
licensing_dir.mkdir(parents=True, exist_ok=True)
db_dir = app_data / "database" / "subsystems"
db_dir.mkdir(parents=True, exist_ok=True)

owner_key = Ed25519PrivateKey.generate()
pub_b64 = base64.b64encode(owner_key.public_key().public_bytes(
    encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)).decode()
(licensing_dir / "trust_store.json").unlink(missing_ok=True)   # re-seed: fresh throwaway owner key each time
OwnerTrustStore(licensing_dir / "trust_store.json").bootstrap_from_anchor(
    {"keys": [{"key_id": "proof-owner-1", "public_key": pub_b64, "algorithm": "ed25519"}]})

signer = WindowsDpapiDeviceIdentityProvider(licensing_dir)
if not signer.has_key():
    signer.generate_new_key()
fingerprint = signer.get_metadata().public_key_fingerprint

now = datetime.now(timezone.utc)
issued = now - timedelta(days=5)   # a 5-day-old lease: the till has been offline since
installation_id = "proof-installation-1"
payload = {
    "assertion_id": "proof-a-1", "issuer": "aura-owner", "product_code": "AURA_RETAIL",
    "license_public_id": "proof-lic-1", "installation_public_id": installation_id,
    "platform": "WINDOWS", "app_version_policy": "1.0.0", "release_channel": "rc",
    "issued_at": issued.isoformat(), "not_before": (issued - timedelta(minutes=5)).isoformat(),
    "expires_at": (issued + timedelta(days=1)).isoformat(),   # Owner's real 24h TTL, long expired
    "license_status": "ACTIVE", "installation_status": "ACTIVE", "subscription_status": status,
    "allowed_device_count": 2, "device_key_fingerprint": fingerprint, "entitlements": {},
    "offline_policy": {
        "check_in_interval_seconds": 86400, "retry_interval_seconds": 3600,
        "offline_grace_seconds": 1209600, "warning_start_seconds": 864000,
        "hard_expiry_behavior": "WARN_ONLY", "clock_rollback_tolerance_seconds": 300,
        "assertion_refresh_threshold_seconds": 86400},
    "contract_version": "v1", "plan_code": "RETAIL_TRIAL", "term_start": "2026-10-13",
    "term_end": term_end, "pilot_status": "ACTIVE" if status == "PILOT" else None,
}
if grace_end:
    payload["commercial_grace_end"] = grace_end
envelope = {
    "payload": payload, "signing_key_id": "proof-owner-1", "algorithm": "ed25519", "assertion_version": 1,
    "signature": base64.b64encode(owner_key.sign(canonicalize_bytes(payload))).decode(),
}
LicenseStateRepository(db_dir / "licensing.db").save(LicenseStateRecord(
    licensing_schema_version=LICENSING_SCHEMA_VERSION, product_code="AURA_RETAIL", platform="WINDOWS",
    current_state="ACTIVE_ONLINE", owner_installation_id=installation_id, device_public_key_fingerprint=fingerprint,
    license_status="ACTIVE", installation_status="ACTIVE", subscription_status=status,
    assertion_envelope_json=json.dumps(envelope), assertion_id="proof-a-1",
    assertion_issued_at=issued.isoformat(), assertion_expires_at=(issued + timedelta(days=1)).isoformat(),
    trusted_time_anchor_server_time=issued.isoformat(), last_successful_checkin_at=issued.isoformat(),
    entitlements_json="{}"))
print(json.dumps({"fingerprint": fingerprint, "term_end": term_end, "status": status,
                  "stored_state": "ACTIVE_ONLINE"}))
