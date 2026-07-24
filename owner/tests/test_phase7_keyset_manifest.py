"""Phase 7 Part D -- signed key-set manifest (additive extension to Phase 6's
/api/licensing/v1/signing-keys). See
docs/licensing/phase7/product-trust-bootstrap-design.md (ADR-7.2)."""
from __future__ import annotations

import base64

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


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
