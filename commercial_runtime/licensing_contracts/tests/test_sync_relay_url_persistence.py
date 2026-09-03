"""Launch-readiness (2026-09-03): sync_relay_base_url persistence.

Owner tells a device where its shop syncs at the exact moment the device
has just proved it holds a valid licence (activation) -- the same moment it
already learns its server-assigned owner_installation_id (see activation.py's
ingest_activation_response()). This file covers the client-side persistence
half of the design; owner/tests/ covers the response-shape half.

Also proves the schema-migration half: an existing licensing.db created
before this column existed must still open and read correctly.
"""
from __future__ import annotations

import base64
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from commercial_runtime.licensing_contracts.activation import ingest_activation_response
from commercial_runtime.licensing_contracts.canonical import canonicalize_bytes
from commercial_runtime.licensing_contracts.events import LicensingEventRecorder
from commercial_runtime.licensing_contracts.state_repository import LicenseStateRepository
from commercial_runtime.licensing_contracts.trust_store import OwnerTrustStore

NOW = datetime.now(timezone.utc)
DEVICE_FINGERPRINT = "fp-device-1"


def _b64_pub(private_key):
    raw = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )
    return base64.b64encode(raw).decode("ascii")


def _payload(installation_id, **overrides):
    base = {
        "assertion_id": "a-1",
        "issuer": "aura-owner",
        "product_code": "AURA_RETAIL",
        "license_public_id": "lic-1",
        "installation_public_id": installation_id,
        "platform": "WINDOWS",
        "app_version_policy": "1.0.0-rc.2",
        "release_channel": "rc",
        "issued_at": NOW.isoformat(),
        "not_before": (NOW - timedelta(minutes=5)).isoformat(),
        "expires_at": (NOW + timedelta(days=1)).isoformat(),
        "license_status": "ACTIVE",
        "installation_status": "ACTIVE",
        "subscription_status": "ACTIVE",
        "allowed_device_count": 2,
        "device_key_fingerprint": DEVICE_FINGERPRINT,
        "entitlements": {"max_devices": 2},
        "offline_policy": {
            "check_in_interval_seconds": 86400,
            "retry_interval_seconds": 3600,
            "offline_grace_seconds": 1209600,
            "warning_start_seconds": 864000,
            "hard_expiry_behavior": "RESTRICT_COMMERCIAL_FEATURES_PRESERVE_DATA",
            "clock_rollback_tolerance_seconds": 300,
            "assertion_refresh_threshold_seconds": 86400,
        },
        "contract_version": "v1",
    }
    base.update(overrides)
    return base


def _envelope(owner_key, key_id, payload):
    sig = owner_key.sign(canonicalize_bytes(payload))
    return {
        "payload": payload,
        "signing_key_id": key_id,
        "algorithm": "ed25519",
        "assertion_version": 1,
        "signature": base64.b64encode(sig).decode("ascii"),
    }


@pytest.fixture
def owner_key():
    return Ed25519PrivateKey.generate()


@pytest.fixture
def trust_store(tmp_path, owner_key):
    s = OwnerTrustStore(tmp_path / "trust.json")
    s.bootstrap_from_anchor({"keys": [{"key_id": "owner-1", "public_key": _b64_pub(owner_key), "algorithm": "ed25519"}]})
    return s


@pytest.fixture
def state_repo(tmp_path):
    return LicenseStateRepository(tmp_path / "database" / "subsystems" / "licensing.db")


@pytest.fixture
def events(tmp_path):
    return LicensingEventRecorder(tmp_path / "database" / "subsystems" / "licensing.db")


def _ingest(trust_store, state_repo, events, response):
    return ingest_activation_response(
        response,
        trust_store=trust_store,
        state_repository=state_repo,
        event_recorder=events,
        product_code="AURA_RETAIL",
        platform="WINDOWS",
        device_public_key_fingerprint=DEVICE_FINGERPRINT,
    )


# ── Persistence ─────────────────────────────────────────────────────────


def test_successful_activation_persists_sync_relay_base_url(owner_key, trust_store, state_repo, events):
    envelope = _envelope(owner_key, "owner-1", _payload("owner-assigned-inst-1"))
    response = {
        "result": "SUCCESS",
        "installation_id": "owner-assigned-inst-1",
        "signed_assertion": envelope,
        "sync_relay_base_url": "https://relay.actionaura.example",
    }

    _ingest(trust_store, state_repo, events, response)

    loaded = state_repo.load()
    assert loaded.sync_relay_base_url == "https://relay.actionaura.example"


def test_activation_response_without_the_field_leaves_it_none(owner_key, trust_store, state_repo, events):
    envelope = _envelope(owner_key, "owner-1", _payload("owner-assigned-inst-2"))
    response = {
        "result": "SUCCESS",
        "installation_id": "owner-assigned-inst-2",
        "signed_assertion": envelope,
        # No sync_relay_base_url key at all -- the byte-identical-when-
        # unset Owner deploy case.
    }

    _ingest(trust_store, state_repo, events, response)

    loaded = state_repo.load()
    assert loaded.sync_relay_base_url is None


def test_rereading_the_record_returns_the_persisted_value(owner_key, trust_store, state_repo, events):
    envelope = _envelope(owner_key, "owner-1", _payload("owner-assigned-inst-3"))
    response = {
        "result": "SUCCESS",
        "installation_id": "owner-assigned-inst-3",
        "signed_assertion": envelope,
        "sync_relay_base_url": "https://relay2.actionaura.example",
    }
    _ingest(trust_store, state_repo, events, response)

    # A brand-new LicenseStateRepository instance against the SAME db path
    # (e.g. a fresh process on the next launch) must see the same value.
    reopened = LicenseStateRepository(state_repo._db_path)
    loaded = reopened.load()
    assert loaded.sync_relay_base_url == "https://relay2.actionaura.example"


# ── Migration: an existing licensing.db predating this column ─────────────


def _old_schema_create_table_sql():
    """A byte-for-byte copy of the PRE-this-change _CREATE_TABLE_SQL --
    deliberately hand-duplicated (not imported) so this test keeps proving
    the migration even if the live _CREATE_TABLE_SQL in state_repository.py
    is edited again later; importing it would let the two drift together
    and silently stop testing the old-schema case."""
    return """
    CREATE TABLE IF NOT EXISTS licensing_state (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        licensing_schema_version INTEGER NOT NULL,
        product_code TEXT NOT NULL,
        platform TEXT NOT NULL,
        owner_environment_id TEXT,
        owner_installation_id TEXT,
        device_public_key_fingerprint TEXT,
        current_state TEXT NOT NULL,
        assertion_envelope_json TEXT,
        assertion_id TEXT,
        assertion_issued_at TEXT,
        assertion_not_before TEXT,
        assertion_expires_at TEXT,
        trusted_time_anchor_server_time TEXT,
        last_successful_checkin_at TEXT,
        last_sync_result TEXT,
        last_public_reason_code TEXT,
        offline_policy_json TEXT,
        entitlements_json TEXT,
        license_status TEXT,
        installation_status TEXT,
        subscription_status TEXT,
        trusted_signing_key_ids_json TEXT,
        restriction_state_metadata_json TEXT,
        updated_at TEXT NOT NULL
    )
    """


def test_pre_existing_licensing_db_without_the_new_column_still_opens_and_reads(tmp_path):
    db_path = tmp_path / "database" / "subsystems" / "licensing.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    # Simulate a licensing.db that was created and populated by a build
    # from BEFORE this change -- no sync_relay_base_url column exists yet.
    conn = sqlite3.connect(str(db_path))
    conn.execute(_old_schema_create_table_sql())
    conn.execute(
        """
        INSERT INTO licensing_state (
            id, licensing_schema_version, product_code, platform, current_state,
            owner_installation_id, updated_at
        ) VALUES (1, 1, 'AURA_RETAIL', 'WINDOWS', 'ACTIVE_ONLINE', 'pre-existing-inst', '2026-01-01T00:00:00')
        """
    )
    conn.commit()
    conn.close()

    # Opening it with the CURRENT repository must not raise, must add the
    # column, and must preserve the pre-existing data untouched.
    repo = LicenseStateRepository(db_path)
    loaded = repo.load()
    assert loaded is not None
    assert loaded.owner_installation_id == "pre-existing-inst"
    assert loaded.current_state == "ACTIVE_ONLINE"
    assert loaded.sync_relay_base_url is None

    # And it must be fully writable going forward (not just readable) --
    # proves the ADD COLUMN actually landed, not just that stale cached
    # column metadata happened to read back as None.
    loaded.sync_relay_base_url = "https://relay.actionaura.example"
    repo.save(loaded)
    assert repo.load().sync_relay_base_url == "https://relay.actionaura.example"


def test_pre_existing_database_other_rows_are_never_touched_by_the_migration(tmp_path):
    db_path = tmp_path / "database" / "subsystems" / "licensing.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute(_old_schema_create_table_sql())
    conn.execute("CREATE TABLE unrelated_product_table (id INTEGER PRIMARY KEY, data TEXT)")
    conn.execute("INSERT INTO unrelated_product_table (data) VALUES ('customer record')")
    conn.commit()
    conn.close()

    LicenseStateRepository(db_path)  # triggers the migration as a side effect of __init__

    conn = sqlite3.connect(str(db_path))
    remaining = conn.execute("SELECT data FROM unrelated_product_table").fetchall()
    conn.close()
    assert remaining == [("customer record",)]
