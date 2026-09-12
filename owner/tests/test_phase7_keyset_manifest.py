"""Phase 7 Part D -- signed key-set manifest (additive extension to Phase 6's
/api/licensing/v1/signing-keys). See
docs/licensing/phase7/product-trust-bootstrap-design.md (ADR-7.2).

Also covers the launch-readiness key-continuity fix: the manifest is
countersigned by every retained non-revoked key, so a rotation can actually
reach a fielded client that still holds only the outgoing key.
"""
from __future__ import annotations

import base64
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

# commercial_runtime lives at the repo root (a sibling of owner/); conftest.py
# only puts owner/ on sys.path. The end-to-end rotation tests below genuinely
# need the real product-side trust store and assertion verifier -- proving
# rotation propagates against an Owner-side re-implementation of the client
# rules would prove nothing. Same scoped path addition
# test_phase8v_scenario_live_server.py already uses.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


def test_manifest_includes_original_shape_unchanged(app, signing_key):
    with app.app_context():
        from app.licensing_service.signing import export_public_keys, export_signed_keyset_manifest

        manifest = export_signed_keyset_manifest(app.config["SIGNING_KEY_DIRECTORY"])
        assert manifest["schema_version"] == 1
        assert manifest["keys"] == export_public_keys()


def test_manifest_is_signed_by_the_active_key(app, signing_key):
    with app.app_context():
        from app.licensing_service.signing import export_signed_keyset_manifest, get_active_signing_key

        manifest = export_signed_keyset_manifest(app.config["SIGNING_KEY_DIRECTORY"])
        active = get_active_signing_key()
        assert manifest["signed_by_key_id"] == active.key_id


def test_manifest_signature_verifies_against_the_published_public_key(app, signing_key):
    with app.app_context():
        from app.licensing_service.canonical import canonicalize_bytes
        from app.licensing_service.signing import export_signed_keyset_manifest, get_active_signing_key

        manifest = export_signed_keyset_manifest(app.config["SIGNING_KEY_DIRECTORY"])
        active = get_active_signing_key()

        signable = {
            "manifest_version": manifest["manifest_version"],
            "issued_at": manifest["issued_at"],
            "keys": manifest["keys"],
        }
        public_key = Ed25519PublicKey.from_public_bytes(base64.b64decode(active.public_key))
        signature = base64.b64decode(manifest["signature"])
        public_key.verify(signature, canonicalize_bytes(signable))  # raises InvalidSignature on failure


def test_manifest_tampered_keys_fail_verification(app, signing_key):
    with app.app_context():
        from cryptography.exceptions import InvalidSignature

        from app.licensing_service.canonical import canonicalize_bytes
        from app.licensing_service.signing import export_signed_keyset_manifest, get_active_signing_key

        manifest = export_signed_keyset_manifest(app.config["SIGNING_KEY_DIRECTORY"])
        active = get_active_signing_key()

        tampered_keys = manifest["keys"] + [{"key_id": "injected", "public_key": "x"}]
        signable = {"manifest_version": manifest["manifest_version"], "issued_at": manifest["issued_at"], "keys": tampered_keys}
        public_key = Ed25519PublicKey.from_public_bytes(base64.b64decode(active.public_key))
        signature = base64.b64decode(manifest["signature"])
        with pytest.raises(InvalidSignature):
            public_key.verify(signature, canonicalize_bytes(signable))


def test_manifest_gracefully_omits_signature_fields_when_no_active_key(app):
    with app.app_context():
        from app.licensing_service.signing import export_signed_keyset_manifest

        manifest = export_signed_keyset_manifest(app.config["SIGNING_KEY_DIRECTORY"])
        assert manifest == {"schema_version": 1, "keys": []}
        assert "signature" not in manifest


def test_manifest_rotation_includes_both_retired_and_active_keys(app, signing_key):
    with app.app_context():
        from app.licensing_service.signing import export_signed_keyset_manifest, rotate_signing_key

        rotate_signing_key(app.config["SIGNING_KEY_DIRECTORY"], "test_rotation_for_manifest")
        manifest = export_signed_keyset_manifest(app.config["SIGNING_KEY_DIRECTORY"])
        statuses = {k["status"] for k in manifest["keys"]}
        assert "ACTIVE" in statuses
        assert "RETIRED" in statuses
        assert len(manifest["keys"]) == 2


def test_signing_keys_route_returns_signed_manifest(app, client, signing_key):
    app.config["EXTERNAL_API_ENABLED"] = True
    resp = client.get("/api/licensing/v1/signing-keys")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["schema_version"] == 1
    assert "signature" in body
    assert "signed_by_key_id" in body


# ── Key continuity (launch-readiness CRITICAL) ────────────────────────────
# Before this, the manifest announcing key N+1 was signed ONLY by key N+1,
# which no fielded client trusted yet -- so a rotation could never propagate
# and every activation/check-in failed UNKNOWN_SIGNING_KEY until a new
# installer shipped.


def _verify_entry(manifest, signature_b64, public_key_b64):
    from app.licensing_service.canonical import canonicalize_bytes

    signable = {
        "manifest_version": manifest["manifest_version"],
        "issued_at": manifest["issued_at"],
        "keys": manifest["keys"],
    }
    Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key_b64)).verify(
        base64.b64decode(signature_b64), canonicalize_bytes(signable)
    )


def test_manifest_is_countersigned_by_every_retained_non_revoked_key(app, signing_key):
    """The outgoing key must countersign the manifest that announces its
    successor -- otherwise no fielded client can ever admit the successor."""
    with app.app_context():
        from sqlalchemy import select

        from app.extensions import db_session
        from app.licensing_service.signing import export_signed_keyset_manifest, rotate_signing_key
        from app.models.licensing_service import SigningKey

        rotate_signing_key(app.config["SIGNING_KEY_DIRECTORY"], "continuity_test")
        manifest = export_signed_keyset_manifest(app.config["SIGNING_KEY_DIRECTORY"])

        signed_by = {entry["key_id"] for entry in manifest["signatures"]}
        assert signed_by == {signing_key, manifest["signed_by_key_id"]}

        published = {r.key_id: r.public_key for r in db_session.execute(select(SigningKey)).scalars().all()}
        for entry in manifest["signatures"]:
            _verify_entry(manifest, entry["signature"], published[entry["key_id"]])  # raises on failure


def test_manifest_countersignatures_exclude_revoked_keys(app, signing_key):
    """A REVOKED key is compromised -- letting it countersign would let whoever
    holds it inject arbitrary keys into every fielded trust store forever."""
    with app.app_context():
        from app.licensing_service.signing import export_signed_keyset_manifest, revoke_signing_key, rotate_signing_key

        rotate_signing_key(app.config["SIGNING_KEY_DIRECTORY"], "continuity_test")
        revoke_signing_key(signing_key, "compromise_drill")
        manifest = export_signed_keyset_manifest(app.config["SIGNING_KEY_DIRECTORY"])

        assert [entry["key_id"] for entry in manifest["signatures"]] == [manifest["signed_by_key_id"]]
        assert signing_key != manifest["signed_by_key_id"]


def test_manifest_legacy_signature_slot_is_unchanged_for_precontinuity_clients(app, signing_key):
    """Additive only: already-fielded clients read signed_by_key_id/signature
    and must keep seeing exactly the active key's signature there."""
    with app.app_context():
        from app.licensing_service.signing import (
            export_signed_keyset_manifest,
            get_active_signing_key,
            rotate_signing_key,
        )

        rotate_signing_key(app.config["SIGNING_KEY_DIRECTORY"], "continuity_test")
        manifest = export_signed_keyset_manifest(app.config["SIGNING_KEY_DIRECTORY"])
        active = get_active_signing_key()

        assert manifest["signed_by_key_id"] == active.key_id
        _verify_entry(manifest, manifest["signature"], active.public_key)


def _client_payload(installation_id, fingerprint):
    now = datetime.now(timezone.utc)
    return {
        "assertion_id": "a-rotation-1",
        "issuer": "aura-owner",
        "product_code": "AURA_RETAIL",
        "license_public_id": "lic-rotation-1",
        "installation_public_id": installation_id,
        "platform": "WINDOWS",
        "issued_at": now.isoformat(),
        "not_before": (now - timedelta(minutes=5)).isoformat(),
        "expires_at": (now + timedelta(days=1)).isoformat(),
        "license_status": "ACTIVE",
        "installation_status": "ACTIVE",
        "subscription_status": "ACTIVE",
        "device_key_fingerprint": fingerprint,
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


def test_rotation_propagates_end_to_end_to_a_client_holding_only_the_old_anchor(app, signing_key, tmp_path):
    """The defect this whole change exists for, proven with the REAL client
    code: bootstrap a trust store from the pre-rotation anchor, rotate Owner's
    key, serve the new manifest, and confirm the client both admits the new
    key and verifies an assertion actually signed by it."""
    from commercial_runtime.licensing_contracts.assertion_verifier import verify_assertion
    from commercial_runtime.licensing_contracts.canonical import canonicalize_bytes as client_canonicalize
    from commercial_runtime.licensing_contracts.trust_store import OwnerTrustStore

    with app.app_context():
        from app.licensing_service.signing import (
            export_signed_keyset_manifest,
            get_active_signing_key,
            load_private_key,
            rotate_signing_key,
        )

        key_directory = app.config["SIGNING_KEY_DIRECTORY"]
        old_key = get_active_signing_key()

        # Exactly what scripts/generate_trust_anchor.py bundles into a build:
        # the single ACTIVE key at build time, nothing else.
        store = OwnerTrustStore(tmp_path / "trust_store.json")
        store.bootstrap_from_anchor(
            {"keys": [{"key_id": old_key.key_id, "public_key": old_key.public_key, "algorithm": "ed25519"}]}
        )

        rotate_signing_key(key_directory, "end_to_end_rotation")
        new_key = get_active_signing_key()
        assert new_key.key_id != old_key.key_id
        assert store.is_trusted(new_key.key_id) is False  # the pre-fix dead end

        store.admit_manifest(export_signed_keyset_manifest(key_directory))
        assert store.is_trusted(new_key.key_id) is True

        payload = _client_payload("inst-rotation-1", "fp-rotation-1")
        signature = load_private_key(key_directory, new_key.key_id).sign(client_canonicalize(payload))
        envelope = {
            "payload": payload,
            "signing_key_id": new_key.key_id,
            "algorithm": "ed25519",
            "signature": base64.b64encode(signature).decode("ascii"),
        }

        verified = verify_assertion(
            envelope,
            trust_store=store,
            expected_product_code="AURA_RETAIL",
            expected_platform="WINDOWS",
            expected_installation_id="inst-rotation-1",
            expected_device_key_fingerprint="fp-rotation-1",
            trusted_now=datetime.now(timezone.utc),
        )
        assert verified.signing_key_id == new_key.key_id


def test_a_manifest_from_a_never_trusted_owner_is_rejected_by_a_real_client(app, signing_key, tmp_path):
    """Same wiring as above, but the client's anchor comes from a DIFFERENT
    Owner instance. Every signer on the manifest is unknown to it, so nothing
    is admitted -- continuity must never degrade into trust-on-first-use."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from commercial_runtime.licensing_contracts.trust_store import OwnerTrustStore

    with app.app_context():
        from app.licensing_service.signing import (
            export_signed_keyset_manifest,
            get_active_signing_key,
            rotate_signing_key,
        )

        foreign = Ed25519PrivateKey.generate()
        foreign_pub = base64.b64encode(
            foreign.public_key().public_bytes(
                encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
            )
        ).decode("ascii")

        store = OwnerTrustStore(tmp_path / "trust_store.json")
        store.bootstrap_from_anchor(
            {"keys": [{"key_id": "other-owner-1", "public_key": foreign_pub, "algorithm": "ed25519"}]}
        )

        rotate_signing_key(app.config["SIGNING_KEY_DIRECTORY"], "foreign_rejection_test")
        store.admit_manifest(export_signed_keyset_manifest(app.config["SIGNING_KEY_DIRECTORY"]))

        assert store.is_trusted(get_active_signing_key().key_id) is False
        assert store.is_trusted(signing_key) is False
