"""Android bridge DeviceIdentityProvider (Part H) -- the "future Android-
Python bridge" device_identity.py's own docstring anticipated.

On Android the Ed25519 device keypair is generated, wrapped (AndroidKeystore
AES-GCM), and used to sign every Owner-facing request entirely in Kotlin (see
android/aura-{clinic,retail}/.../licensing/DeviceIdentity.kt) -- this process
never holds, and never needs, the private key. What it DOES need is read
access to the two plaintext-safe files Kotlin's DeviceIdentity already writes
next to the (Python-inaccessible) encrypted key file, in the same directory
this provider is constructed against:

    <licensing_dir>/device_public_key.txt   -- base64 raw Ed25519 public key
    <licensing_dir>/device_key_meta.json    -- {algorithm, publicKeyFingerprint,
                                                 createdAt, status,
                                                 ownerInstallationId}

Kotlin's DeviceIdentity must be constructed with baseDir = <app_data_dir> (the
same directory Python's AURA_APP_DATA resolves to, i.e. filesDir/data on
Android -- see android_platform.py), NOT context.filesDir directly, or these
two processes end up looking at different directories. See
docs/licensing/phase7/android-device-key-storage-design.md.

generate_new_key() and sign() are Kotlin's job exclusively (Part U: Kotlin
signs, Python is sole authority for verification) -- calling either here
would mean this process either can't produce a real signature at all, or
would need its own competing key, so both raise instead of silently doing
the wrong thing. mark_reset_pending()/destroy_key() are plain metadata
writes/deletes with no cryptography involved, so either platform's process
can safely perform them.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .device_identity import DeviceIdentityError, DeviceIdentityProvider, DeviceKeyMetadata, LocalStateCorruptError


class AndroidBridgeDeviceIdentityProvider(DeviceIdentityProvider):
    def __init__(self, licensing_dir: Path):
        self._public_key_path = licensing_dir / "device_public_key.txt"
        self._meta_path = licensing_dir / "device_key_meta.json"

    def has_key(self) -> bool:
        return self._public_key_path.exists() and self._meta_path.exists()

    def generate_new_key(self) -> DeviceKeyMetadata:
        raise DeviceIdentityError(
            "Device key generation is owned by the Kotlin layer on Android "
            "(DeviceIdentity.generateNewKey()) -- this bridge is read-only."
        )

    def get_public_key_b64(self) -> str:
        if not self._public_key_path.exists():
            raise LocalStateCorruptError("Device public key file is missing.")
        return self._public_key_path.read_text(encoding="utf-8").strip()

    def get_metadata(self) -> DeviceKeyMetadata:
        if not self._meta_path.exists():
            raise LocalStateCorruptError("Device key metadata file is missing.")
        try:
            raw = json.loads(self._meta_path.read_text(encoding="utf-8"))
            return DeviceKeyMetadata(
                algorithm=raw["algorithm"],
                public_key_fingerprint=raw["publicKeyFingerprint"],
                created_at=datetime.fromisoformat(raw["createdAt"]),
                status=raw["status"],
                owner_installation_id=raw.get("ownerInstallationId"),
            )
        except (KeyError, ValueError, json.JSONDecodeError) as exc:
            raise LocalStateCorruptError(f"Device key metadata is unreadable: {exc}") from exc

    def sign(self, canonical_bytes: bytes) -> str:
        raise DeviceIdentityError(
            "Signing is owned by the Kotlin layer on Android (DeviceIdentity.sign()) "
            "-- this process never holds the private key."
        )

    def mark_reset_pending(self) -> None:
        meta = self.get_metadata()
        self._write_meta(meta.algorithm, meta.public_key_fingerprint, meta.created_at, "RESET_PENDING", meta.owner_installation_id)

    def destroy_key(self) -> None:
        # Mirrors Kotlin's own DeviceIdentity.destroyKey() -- deletes the same
        # three files that method deletes (device_key.enc is untouched by
        # this class since it never reads it, but unlinking it here too keeps
        # the deletion atomic-enough from whichever side calls it first).
        if self._public_key_path.exists():
            self._public_key_path.unlink()
        if self._meta_path.exists():
            self._meta_path.unlink()
        key_file = self._meta_path.with_name("device_key.enc")
        if key_file.exists():
            key_file.unlink()

    def _write_meta(self, algorithm: str, fingerprint: str, created_at: datetime, status: str, owner_installation_id) -> None:
        payload = {
            "algorithm": algorithm,
            "publicKeyFingerprint": fingerprint,
            "createdAt": created_at.isoformat(),
            "status": status,
            "ownerInstallationId": owner_installation_id,
        }
        self._meta_path.write_text(json.dumps(payload), encoding="utf-8")
