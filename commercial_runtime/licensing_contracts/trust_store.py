"""OwnerTrustStore (Part D) -- the set of Owner signing keys this
installation currently trusts.

See docs/licensing/phase7/product-trust-bootstrap-design.md (ADR-7.2) for
the full bootstrap/rotation/revocation design. This module holds trust
STATE and the pure rules for updating it; it never makes an HTTP call
itself (that's LicensingClient) and never verifies a signature itself
(that's AssertionVerifier) -- it only answers "is this key_id currently
trusted, and with what public key."
"""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from .canonical import canonicalize_bytes
from .device_identity import verify_signature


class TrustStoreError(ValueError):
    pass


@dataclass(frozen=True)
class TrustedKey:
    key_id: str
    public_key_b64: str
    algorithm: str
    status: str  # "ACTIVE" | "RETIRED" -- REVOKED keys are removed, not stored with this status
    source: str  # "BUNDLED_ANCHOR" | "ROTATION_MANIFEST"


class OwnerTrustStore:
    """Persisted as a small JSON file, separate from the main license state
    repository (Part J excludes it deliberately -- trust state is
    infrastructure, not license state, and must survive a license-state
    reset). Loaded once, mutated only through admit_manifest()/revoke()."""

    def __init__(self, path: Path):
        self._path = path
        self._keys: dict[str, TrustedKey] = {}
        if path.exists():
            self._load()

    def bootstrap_from_anchor(self, anchor_json: dict) -> None:
        """Called once, at first run, from the bundled trust_anchor.json
        (Part D step 1). Refuses to run if keys are already loaded -- the
        anchor is a first-run seed, never a way to reset trust later (that
        would reopen the TOFU risk Part D explicitly forbids)."""
        if self._keys:
            raise TrustStoreError("Trust store already initialized -- refusing to re-bootstrap from anchor.")
        for entry in anchor_json.get("keys", []):
            key = TrustedKey(
                key_id=entry["key_id"],
                public_key_b64=entry["public_key"],
                algorithm=entry.get("algorithm", "ed25519"),
                status="ACTIVE",
                source="BUNDLED_ANCHOR",
            )
            self._keys[key.key_id] = key
        if not self._keys:
            raise TrustStoreError("Bundled trust anchor contains no keys -- refusing an empty trust store.")
        self._save()

    def is_trusted(self, key_id: str) -> bool:
        return key_id in self._keys

    def get_public_key_b64(self, key_id: str) -> Optional[str]:
        key = self._keys.get(key_id)
        return key.public_key_b64 if key else None

    def admit_manifest(self, manifest: dict) -> None:
        """Part D step 3: admit a signed key-set manifest only if it is
        itself signed by a key already in this trust store. A manifest
        signed by an unknown key is discarded silently -- it never partially
        updates the trusted set."""
        signed_by = manifest.get("signed_by_key_id")
        if not signed_by or not self.is_trusted(signed_by):
            return  # untrusted signer -- discard, do not raise (this is an
            # expected, non-exceptional outcome on every routine check-in
            # where nothing has changed).

        signer_pub_b64 = self.get_public_key_b64(signed_by)
        signable = {
            "manifest_version": manifest.get("manifest_version"),
            "issued_at": manifest.get("issued_at"),
            "keys": manifest.get("keys"),
        }
        try:
            canonical_bytes = canonicalize_bytes(signable)
            signature = base64.b64decode(manifest["signature"])
            signer_pub = base64.b64decode(signer_pub_b64)
        except Exception:
            return  # malformed manifest -- discard, not a hard error.

        if not verify_signature(signer_pub, canonical_bytes, signature):
            return  # bad signature -- discard.

        # Signature verified -- now safe to admit/update/remove keys.
        incoming_ids = set()
        for entry in manifest.get("keys", []):
            key_id = entry["key_id"]
            incoming_ids.add(key_id)
            status = entry.get("status")
            if status == "REVOKED":
                self._keys.pop(key_id, None)
                continue
            if status not in ("ACTIVE", "RETIRED"):
                continue  # unknown status -- never admit a key we can't classify
            self._keys[key_id] = TrustedKey(
                key_id=key_id,
                public_key_b64=entry["public_key"],
                algorithm=entry.get("algorithm", "ed25519"),
                status=status,
                source="ROTATION_MANIFEST",
            )
        self._save()

    def revoke_locally(self, key_id: str) -> None:
        """Emergency local-only revocation (e.g. staff-initiated, out of
        band from a manifest) -- removes trust immediately without waiting
        for the next check-in's manifest."""
        if key_id in self._keys:
            del self._keys[key_id]
            self._save()

    def _load(self) -> None:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise TrustStoreError(f"Trust store file is unreadable: {exc}") from exc
        for entry in raw.get("keys", []):
            key = TrustedKey(
                key_id=entry["key_id"],
                public_key_b64=entry["public_key_b64"],
                algorithm=entry["algorithm"],
                status=entry["status"],
                source=entry["source"],
            )
            self._keys[key.key_id] = key

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "keys": [
                {
                    "key_id": k.key_id,
                    "public_key_b64": k.public_key_b64,
                    "algorithm": k.algorithm,
                    "status": k.status,
                    "source": k.source,
                }
                for k in self._keys.values()
            ]
        }
        self._path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
