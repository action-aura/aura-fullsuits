import base64
import json

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

from commercial_runtime.licensing_contracts.canonical import canonicalize_bytes
from commercial_runtime.licensing_contracts.trust_store import OwnerTrustStore, TrustStoreError


def _b64_pub(private_key):
    raw = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )
    return base64.b64encode(raw).decode("ascii")


def _sign_manifest(private_key, manifest_body):
    signable = {
        "manifest_version": manifest_body["manifest_version"],
        "issued_at": manifest_body["issued_at"],
        "keys": manifest_body["keys"],
    }
    sig = private_key.sign(canonicalize_bytes(signable))
    return {**manifest_body, "signature": base64.b64encode(sig).decode("ascii")}


@pytest.fixture
def anchor_key():
    return Ed25519PrivateKey.generate()


@pytest.fixture
def store(tmp_path, anchor_key):
    s = OwnerTrustStore(tmp_path / "trust_store.json")
    s.bootstrap_from_anchor(
        {"keys": [{"key_id": "anchor-1", "public_key": _b64_pub(anchor_key), "algorithm": "ed25519"}]}
    )
    return s


def test_bootstrap_trusts_anchor_key(store):
    assert store.is_trusted("anchor-1") is True
    assert store.is_trusted("unknown-key") is False


def test_cannot_rebootstrap(store):
    with pytest.raises(TrustStoreError):
        store.bootstrap_from_anchor({"keys": [{"key_id": "x", "public_key": "y"}]})


def test_empty_anchor_rejected(tmp_path):
    s = OwnerTrustStore(tmp_path / "trust_store.json")
    with pytest.raises(TrustStoreError):
        s.bootstrap_from_anchor({"keys": []})


def test_admit_manifest_signed_by_trusted_key_adds_new_key(store, anchor_key):
    new_key_pub = _b64_pub(Ed25519PrivateKey.generate())
    manifest = _sign_manifest(
        anchor_key,
        {
            "manifest_version": 1,
            "issued_at": "2026-07-22T10:00:00+00:00",
            "keys": [
                {"key_id": "anchor-1", "public_key": _b64_pub(anchor_key), "algorithm": "ed25519", "status": "ACTIVE"},
                {"key_id": "rotated-1", "public_key": new_key_pub, "algorithm": "ed25519", "status": "ACTIVE"},
            ],
            "signed_by_key_id": "anchor-1",
        },
    )
    store.admit_manifest(manifest)
    assert store.is_trusted("rotated-1") is True


def test_admit_manifest_signed_by_untrusted_key_silently_discarded(store):
    attacker_key = Ed25519PrivateKey.generate()
    manifest = _sign_manifest(
        attacker_key,
        {
            "manifest_version": 1,
            "issued_at": "2026-07-22T10:00:00+00:00",
            "keys": [{"key_id": "evil-1", "public_key": _b64_pub(attacker_key), "algorithm": "ed25519", "status": "ACTIVE"}],
            "signed_by_key_id": "attacker-claims-anchor-1",
        },
    )
    store.admit_manifest(manifest)
    assert store.is_trusted("evil-1") is False


def test_admit_manifest_with_tampered_signature_discarded(store, anchor_key):
    manifest = _sign_manifest(
        anchor_key,
        {
            "manifest_version": 1,
            "issued_at": "2026-07-22T10:00:00+00:00",
            "keys": [{"key_id": "rotated-1", "public_key": _b64_pub(Ed25519PrivateKey.generate()), "algorithm": "ed25519", "status": "ACTIVE"}],
            "signed_by_key_id": "anchor-1",
        },
    )
    # Tamper with the keys list after signing -- signature no longer matches.
    manifest["keys"][0]["key_id"] = "attacker-injected"
    store.admit_manifest(manifest)
    assert store.is_trusted("attacker-injected") is False


def test_admit_manifest_revokes_key(store, anchor_key):
    manifest = _sign_manifest(
        anchor_key,
        {
            "manifest_version": 1,
            "issued_at": "2026-07-22T10:00:00+00:00",
            "keys": [{"key_id": "anchor-1", "public_key": "", "algorithm": "ed25519", "status": "REVOKED"}],
            "signed_by_key_id": "anchor-1",
        },
    )
    store.admit_manifest(manifest)
    assert store.is_trusted("anchor-1") is False


def test_revoke_locally(store):
    store.revoke_locally("anchor-1")
    assert store.is_trusted("anchor-1") is False


def test_persistence_round_trip(tmp_path, anchor_key):
    path = tmp_path / "trust_store.json"
    s1 = OwnerTrustStore(path)
    s1.bootstrap_from_anchor({"keys": [{"key_id": "anchor-1", "public_key": _b64_pub(anchor_key), "algorithm": "ed25519"}]})

    s2 = OwnerTrustStore(path)
    assert s2.is_trusted("anchor-1") is True
    assert s2.get_public_key_b64("anchor-1") == _b64_pub(anchor_key)


def test_manifest_with_unknown_status_field_not_admitted(store, anchor_key):
    manifest = _sign_manifest(
        anchor_key,
        {
            "manifest_version": 1,
            "issued_at": "2026-07-22T10:00:00+00:00",
            "keys": [{"key_id": "weird-1", "public_key": "x", "algorithm": "ed25519", "status": "SOMETHING_ELSE"}],
            "signed_by_key_id": "anchor-1",
        },
    )
    store.admit_manifest(manifest)
    assert store.is_trusted("weird-1") is False
