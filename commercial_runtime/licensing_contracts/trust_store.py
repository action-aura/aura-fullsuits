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


# Bounded so a hostile or garbled manifest can never turn admission into an
# unbounded verification loop. Owner emits one entry per retained non-revoked
# key; 64 is roughly five years of monthly rotation, far beyond any real
# schedule, and the legacy single-signature slot is checked regardless of how
# full this list is.
MAX_MANIFEST_SIGNATURES = 64


def _candidate_signatures(manifest: dict) -> list[tuple[str, str]]:
    """(key_id, signature_b64) pairs offered by a manifest, most-preferred
    first: the continuity `signatures` list Owner emits since the key-
    continuity fix, then the legacy single `signed_by_key_id`/`signature`
    pair that pre-continuity Owner builds emit on their own.

    Purely structural -- this decides what is worth CHECKING, never what is
    trusted. Every pair still has to name a key already in the store and
    still has to verify against the public key the store already holds for
    it.
    """
    candidates: list[tuple[str, str]] = []

    entries = manifest.get("signatures")
    if isinstance(entries, list):
        for entry in entries[:MAX_MANIFEST_SIGNATURES]:
            if not isinstance(entry, dict):
                continue
            key_id = entry.get("key_id")
            signature = entry.get("signature")
            if isinstance(key_id, str) and isinstance(signature, str):
                candidates.append((key_id, signature))

    legacy_key_id = manifest.get("signed_by_key_id")
    legacy_signature = manifest.get("signature")
    if isinstance(legacy_key_id, str) and isinstance(legacy_signature, str):
        candidates.append((legacy_key_id, legacy_signature))

    return candidates


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

    def trusted_key_ids(self) -> list[str]:
        """Key IDs only -- never public key material, never the file itself.

        Diagnostics only (see activation.py::_failure_details). A key id is a
        public, non-secret identifier that Owner already publishes in every
        assertion envelope it signs, so recording one in the LOCAL event log
        leaks nothing; the corresponding public keys are deliberately NOT
        exposed here, because nothing outside this class needs them to explain
        a failure.
        """
        return sorted(self._keys)

    def admit_manifest(self, manifest: dict) -> None:
        """Part D step 3: admit a signed key-set manifest only if it is
        itself signed by a key already in this trust store. A manifest whose
        signers are all unknown is discarded silently -- it never partially
        updates the trusted set.

        KEY CONTINUITY (launch-readiness CRITICAL fix). This used to look at
        exactly one signature -- `signed_by_key_id`, which Owner always set
        to the CURRENTLY ACTIVE key. That made rotation unpropagatable and
        unrecoverable: right after a rotation the only manifest Owner could
        produce was signed by a key no fielded install had ever trusted, so
        it was discarded here, every subsequent assertion failed
        UNKNOWN_SIGNING_KEY, and only a new installer with a fresh bundled
        anchor could fix it.

        Owner now countersigns the manifest with every retained non-revoked
        key (see owner/app/licensing_service/signing.py::
        export_signed_keyset_manifest), so the OUTGOING key -- which this
        install still trusts -- vouches for the manifest introducing its
        successor. All that changes here is WHICH already-trusted key is
        allowed to be the one that vouches: it no longer has to be Owner's
        current active key. The rule itself is untouched -- a signature is
        still verified against the public key THIS STORE already holds for
        that key_id, so a manifest signed only by keys that were never
        trusted is still rejected, and a forged entry naming a trusted
        key_id still fails verification. Nothing is ever admitted on a
        manifest's own say-so.

        The legacy single-signature shape is still accepted (checked last),
        so an Owner instance that predates the countersigning change keeps
        working unchanged.
        """
        signable = {
            "manifest_version": manifest.get("manifest_version"),
            "issued_at": manifest.get("issued_at"),
            "keys": manifest.get("keys"),
        }
        try:
            canonical_bytes = canonicalize_bytes(signable)
        except Exception:
            return  # malformed manifest -- discard, not a hard error.

        for key_id, signature_b64 in _candidate_signatures(manifest):
            if not self.is_trusted(key_id):
                continue  # expected and non-exceptional -- Owner countersigns
                # with keys this install may never have seen.
            try:
                signature = base64.b64decode(signature_b64)
                signer_pub = base64.b64decode(self.get_public_key_b64(key_id))
            except Exception:
                continue  # malformed entry -- skip it, another may be sound.
            if verify_signature(signer_pub, canonical_bytes, signature):
                break
        else:
            return  # no trusted key vouched for this manifest -- discard.

        # A trusted key's signature verified -- now safe to admit/update/
        # remove keys.
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
