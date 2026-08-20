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


def _signable(manifest_body):
    return {
        "manifest_version": manifest_body["manifest_version"],
        "issued_at": manifest_body["issued_at"],
        "keys": manifest_body["keys"],
    }


def _raw_signature(private_key, manifest_body):
    return base64.b64encode(private_key.sign(canonicalize_bytes(_signable(manifest_body)))).decode("ascii")


def _sign_manifest(private_key, manifest_body):
    return {**manifest_body, "signature": _raw_signature(private_key, manifest_body)}


def _countersign(private_key, key_id, manifest_body):
    """One entry of the `signatures` continuity list Owner now emits (see
    owner/app/licensing_service/signing.py::export_signed_keyset_manifest)."""
    return {"key_id": key_id, "algorithm": "ed25519", "signature": _raw_signature(private_key, manifest_body)}


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


# ── Key continuity (launch-readiness CRITICAL) ────────────────────────────
# A rotation manifest is signed by the OUTGOING key as well as the incoming
# one, so a client that still holds only the old anchor can carry its trust
# across exactly one hop. Without this, rotation is unpropagatable: the
# manifest announcing key N+1 was signed only by key N+1, which no fielded
# client trusts yet.


def _rotation_manifest(anchor_key, anchor_key_id, new_key, new_key_id):
    return {
        "manifest_version": 1,
        "issued_at": "2026-08-19T10:00:00+00:00",
        "keys": [
            {"key_id": anchor_key_id, "public_key": _b64_pub(anchor_key), "algorithm": "ed25519", "status": "RETIRED"},
            {"key_id": new_key_id, "public_key": _b64_pub(new_key), "algorithm": "ed25519", "status": "ACTIVE"},
        ],
        "signed_by_key_id": new_key_id,
    }


def test_admit_manifest_accepts_continuity_signature_from_the_outgoing_key(store, anchor_key):
    """The whole point: the legacy single-signature slot names the NEW active
    key (which this client has never trusted), but the continuity list carries
    the outgoing key's countersignature -- so trust transfers one hop."""
    new_key = Ed25519PrivateKey.generate()
    body = _rotation_manifest(anchor_key, "anchor-1", new_key, "rotated-2")
    manifest = {
        **body,
        "signature": _raw_signature(new_key, body),  # untrusted signer, as after a real rotation
        "signatures": [
            _countersign(new_key, "rotated-2", body),
            _countersign(anchor_key, "anchor-1", body),
        ],
    }
    store.admit_manifest(manifest)
    assert store.is_trusted("rotated-2") is True
    assert store.get_public_key_b64("rotated-2") == _b64_pub(new_key)


def test_admit_manifest_rejects_continuity_signatures_from_never_trusted_keys(store):
    """A manifest countersigned only by keys this store has never trusted is
    still discarded -- continuity widens WHICH trusted key may introduce a
    successor, never whether an untrusted one may."""
    attacker_key = Ed25519PrivateKey.generate()
    second_attacker_key = Ed25519PrivateKey.generate()
    body = {
        "manifest_version": 1,
        "issued_at": "2026-08-19T10:00:00+00:00",
        "keys": [{"key_id": "evil-1", "public_key": _b64_pub(attacker_key), "algorithm": "ed25519", "status": "ACTIVE"}],
        "signed_by_key_id": "evil-0",
    }
    manifest = {
        **body,
        "signature": _raw_signature(attacker_key, body),
        "signatures": [
            _countersign(attacker_key, "evil-0", body),
            _countersign(second_attacker_key, "evil-2", body),
        ],
    }
    store.admit_manifest(manifest)
    assert store.is_trusted("evil-1") is False
    assert store.is_trusted("evil-0") is False


def test_admit_manifest_rejects_continuity_signature_forging_a_trusted_key_id(store, anchor_key):
    """Claiming a trusted key_id proves nothing -- the signature is checked
    against the public key already STORED for that key_id, not one supplied by
    the manifest."""
    attacker_key = Ed25519PrivateKey.generate()
    new_key = Ed25519PrivateKey.generate()
    body = _rotation_manifest(anchor_key, "anchor-1", new_key, "rotated-2")
    manifest = {
        **body,
        "signatures": [_countersign(attacker_key, "anchor-1", body)],
    }
    manifest.pop("signature", None)
    store.admit_manifest(manifest)
    assert store.is_trusted("rotated-2") is False


def test_admit_manifest_ignores_continuity_signatures_beyond_the_bounded_cap(store, anchor_key):
    """A hostile manifest cannot force an unbounded number of signature
    verifications; entries past the cap are never examined."""
    from commercial_runtime.licensing_contracts.trust_store import MAX_MANIFEST_SIGNATURES

    new_key = Ed25519PrivateKey.generate()
    body = _rotation_manifest(anchor_key, "anchor-1", new_key, "rotated-2")
    padding = [{"key_id": "anchor-1", "algorithm": "ed25519", "signature": "AAAA"} for _ in range(MAX_MANIFEST_SIGNATURES)]
    manifest = {**body, "signatures": [*padding, _countersign(anchor_key, "anchor-1", body)]}
    manifest.pop("signature", None)  # no legacy slot -- the capped list is the only path in
    store.admit_manifest(manifest)
    assert store.is_trusted("rotated-2") is False


def test_admit_manifest_still_accepts_the_legacy_single_signature_shape(store, anchor_key):
    """Pre-continuity Owner builds emit only signed_by_key_id/signature. That
    shape must keep working unchanged."""
    new_key_pub = _b64_pub(Ed25519PrivateKey.generate())
    manifest = _sign_manifest(
        anchor_key,
        {
            "manifest_version": 1,
            "issued_at": "2026-08-19T10:00:00+00:00",
            "keys": [{"key_id": "legacy-rotated", "public_key": new_key_pub, "algorithm": "ed25519", "status": "ACTIVE"}],
            "signed_by_key_id": "anchor-1",
        },
    )
    store.admit_manifest(manifest)
    assert store.is_trusted("legacy-rotated") is True


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
