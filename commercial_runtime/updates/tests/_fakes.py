"""Shared test doubles for the update-channel suites (not a test file: the
leading underscore keeps the runner from collecting it).

A fake Owner here signs with a REAL Ed25519 key that is placed in a REAL
OwnerTrustStore on disk -- so a verification test that passes has run the
production verifier against a genuine signature, and a mutation of the
verifier changes the outcome. A stub that returned "valid" would prove nothing.
"""
from __future__ import annotations

import base64
import hashlib
import uuid
from types import SimpleNamespace

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from commercial_runtime.licensing_contracts.canonical import canonicalize_bytes
from commercial_runtime.licensing_contracts.trust_store import OwnerTrustStore

INSTALLATION_ID = "inst-00000000-0000-0000-0000-000000000001"
KEY_ID = "owner-test-key-1"


class OwnerKey:
    def __init__(self, key_id: str = KEY_ID):
        self.key_id = key_id
        self._private = Ed25519PrivateKey.generate()
        raw = self._private.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        self.public_b64 = base64.b64encode(raw).decode()

    def sign_b64(self, payload: dict) -> str:
        return base64.b64encode(self._private.sign(canonicalize_bytes(payload))).decode()

    def envelope(self, payload: dict, *, key_id=None, algorithm="ed25519") -> dict:
        return {
            "payload": payload,
            "signing_key_id": key_id or self.key_id,
            "algorithm": algorithm,
            "signature": self.sign_b64(payload),
        }


def make_trust_store(tmp_path, *keys: OwnerKey) -> OwnerTrustStore:
    store = OwnerTrustStore(tmp_path / "trust_store.json")
    store.bootstrap_from_anchor(
        {"keys": [{"key_id": k.key_id, "public_key": k.public_b64, "algorithm": "ed25519"} for k in keys]}
    )
    return store


def release_payload(*, installation_id=INSTALLATION_ID, request_id, version="1.0.0-rc.9", checksum="a" * 64,
                    size=1024, **overrides) -> dict:
    release = {
        "product_version_id": str(uuid.uuid4()),
        "version": version,
        "build_number": 12,
        "release_channel": "RC",
        "artifact_checksum_sha256": checksum,
        "artifact_size_bytes": size,
        "release_notes": "Faster till.",
        "published_at": "2026-10-08T10:00:00+00:00",
    }
    release.update(overrides.pop("release_overrides", {}))
    payload = {
        "descriptor_version": 1,
        "installation_id": installation_id,
        "request_id": request_id,
        "issued_at": "2026-10-08T10:00:00+00:00",
        "current_version": "1.0.0-rc.8",
        "update_available": True,
        "update_required": False,
        "below_min_supported": False,
        "release": release,
    }
    payload.update(overrides)
    return payload


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class FakeStateRepository:
    def __init__(self, installation_id=INSTALLATION_ID):
        self._record = SimpleNamespace(owner_installation_id=installation_id) if installation_id else None

    def load(self):
        return self._record


class FakeOwnerClient:
    """Stands in for LicensingClient. Counts every call so a test can assert
    the check RAN (or, for a disabled install, that nothing was ever called)."""

    def __init__(self, key: OwnerKey, *, version="1.0.0-rc.9", payload_bytes=b"installer-bytes" * 100,
                 serve_bytes=None, advertise_checksum=None, advertise_size=None, update_available=True,
                 descriptor_overrides=None, authorize_response=None, transport_error=None):
        self.key = key
        self.version = version
        self.payload_bytes = payload_bytes
        self.serve_bytes = payload_bytes if serve_bytes is None else serve_bytes
        self.advertise_checksum = advertise_checksum or sha256_hex(payload_bytes)
        self.advertise_size = advertise_size or len(payload_bytes)
        self.update_available = update_available
        self.descriptor_overrides = descriptor_overrides or {}
        self.authorize_response = authorize_response
        self.transport_error = transport_error
        self.find_calls = []
        self.authorize_calls = []
        self.download_calls = []

    def find_latest_release(self, *, installation_id, app_version, release_channel, signer, request_id=None):
        self.find_calls.append({"installation_id": installation_id, "app_version": app_version,
                                "release_channel": release_channel, "request_id": request_id})
        if self.transport_error is not None:
            raise self.transport_error
        if not self.update_available:
            payload = release_payload(installation_id=installation_id, request_id=request_id)
            payload.update(update_available=False, release=None)
        else:
            payload = release_payload(installation_id=installation_id, request_id=request_id,
                                      version=self.version, checksum=self.advertise_checksum,
                                      size=self.advertise_size)
        payload.update(self.descriptor_overrides)
        return {"contract_version": "v1", "release_descriptor": self.key.envelope(payload)}

    def authorize_release_download(self, *, installation_id, product_version_id, signer, request_id=None):
        self.authorize_calls.append({"installation_id": installation_id, "product_version_id": product_version_id})
        if self.authorize_response is not None:
            return self.authorize_response
        return {"download_token": "single-use-token", "expires_at": "2026-10-08T11:00:00+00:00"}

    def download_release(self, token, dest_path, *, expected_size, chunk=1024 * 1024, progress=None):
        self.download_calls.append({"token": token, "expected_size": expected_size})
        with open(dest_path, "wb") as out:
            out.write(self.serve_bytes)
        if progress is not None:
            progress(len(self.serve_bytes))


class Launcher:
    """Counts launches: the install tests assert ZERO calls on a refusal, which
    is the only way to tell 'refused before launching' from 'launched and then
    something else failed'."""

    def __init__(self, raises=None):
        self.calls = []
        self.raises = raises

    def __call__(self, path, args):
        self.calls.append((path, list(args)))
        if self.raises is not None:
            raise self.raises
