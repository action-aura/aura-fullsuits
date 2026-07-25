"""Device cryptographic identity and proof-of-possession verification (Part D)."""
from __future__ import annotations

import base64
import hashlib
from datetime import datetime, timezone

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from sqlalchemy import select

from app.extensions import db_session
from app.models.licensing_service import DevicePublicKey

SUPPORTED_ALGORITHMS = ("ed25519",)


class DeviceIdentityError(ValueError):
    pass


def fingerprint_of(raw_public_key_b64: str) -> str:
    return hashlib.sha256(base64.b64decode(raw_public_key_b64)).hexdigest()


def validate_public_key(algorithm: str, public_key_b64: str) -> Ed25519PublicKey:
    if algorithm not in SUPPORTED_ALGORITHMS:
        raise DeviceIdentityError("INVALID_PUBLIC_KEY")
    try:
        raw = base64.b64decode(public_key_b64, validate=True)
    except Exception:
        raise DeviceIdentityError("INVALID_PUBLIC_KEY")
    if len(raw) != 32:  # Ed25519 raw public keys are exactly 32 bytes
        raise DeviceIdentityError("INVALID_PUBLIC_KEY")
    try:
        return Ed25519PublicKey.from_public_bytes(raw)
    except Exception:
        raise DeviceIdentityError("INVALID_PUBLIC_KEY")


def load_public_key_or_none(public_key_b64: str) -> Ed25519PublicKey | None:
    """Reloads an already-registered (already-validated-at-registration-time)
    public key for verification. Returns None rather than raising on
    corruption -- callers treat that identically to a signature failure."""
    try:
        raw = base64.b64decode(public_key_b64, validate=True)
        if len(raw) != 32:
            return None
        return Ed25519PublicKey.from_public_bytes(raw)
    except Exception:
        return None


def verify_signature(public_key: Ed25519PublicKey, canonical_bytes: bytes, signature_b64: str) -> bool:
    try:
        signature = base64.b64decode(signature_b64, validate=True)
    except Exception:
        return False
    try:
        public_key.verify(signature, canonical_bytes)
        return True
    except InvalidSignature:
        return False
    except Exception:
        return False


def register_device_key(installation_id, algorithm: str, public_key_b64: str) -> DevicePublicKey:
    fingerprint = fingerprint_of(public_key_b64)
    row = DevicePublicKey(
        installation_id=installation_id, public_key=public_key_b64, fingerprint=fingerprint, algorithm=algorithm,
        status="ACTIVE", last_proof_at=datetime.now(timezone.utc),
    )
    db_session.add(row)
    db_session.flush()
    return row


def get_active_device_key(installation_id) -> DevicePublicKey | None:
    return db_session.execute(
        select(DevicePublicKey).where(DevicePublicKey.installation_id == installation_id, DevicePublicKey.status == "ACTIVE")
    ).scalars().first()


def get_device_key_by_fingerprint(fingerprint: str) -> DevicePublicKey | None:
    """fingerprint is globally UNIQUE (one physical device identity maps to
    at most one row ever) -- used to recognize a device retrying activation
    under a fresh self-generated installation_id (the client-generated
    installation_id is fresh on every attempt by protocol design, so it
    cannot be used alone to detect a retry of a request whose response the
    client never received)."""
    return db_session.execute(
        select(DevicePublicKey).where(DevicePublicKey.fingerprint == fingerprint)
    ).scalars().first()


def get_most_recent_device_key(installation_id) -> DevicePublicKey | None:
    """Regardless of status -- used only where an already-revoked key must
    still be verifiable against (an idempotent retry of the very deactivation
    request that revoked it). Never used for check-in, which must always
    require a currently-ACTIVE key."""
    return db_session.execute(
        select(DevicePublicKey).where(DevicePublicKey.installation_id == installation_id).order_by(DevicePublicKey.created_at.desc())
    ).scalars().first()


def record_successful_proof(device_key: DevicePublicKey) -> None:
    device_key.last_proof_at = datetime.now(timezone.utc)
    db_session.commit()


def revoke_device_key(device_key: DevicePublicKey, actor_staff_user_id=None) -> None:
    from app.audit.services import record as audit_record

    device_key.status = "REVOKED"
    device_key.revoked_at = datetime.now(timezone.utc)
    db_session.commit()
    audit_record(
        actor_staff_user_id=actor_staff_user_id, actor_role_snapshot=None, action_code="DEVICE_KEY_REVOKED",
        entity_type="device_public_key", entity_public_id=str(device_key.id),
    )


def replace_device_key(old_device_key: DevicePublicKey, new_algorithm: str, new_public_key_b64: str, actor_staff_user_id=None) -> DevicePublicKey:
    """Old key is revoked (not silently left active); new key requires its
    own fresh proof of possession on the next request -- an old, replaced
    device can never be silently reactivated (Part Q)."""
    from app.audit.services import record as audit_record

    new_key = register_device_key(old_device_key.installation_id, new_algorithm, new_public_key_b64)
    old_device_key.status = "REPLACED"
    old_device_key.replaced_by_device_key_id = new_key.id
    db_session.commit()
    audit_record(
        actor_staff_user_id=actor_staff_user_id, actor_role_snapshot=None, action_code="DEVICE_REPLACED",
        entity_type="installation", entity_public_id=str(old_device_key.installation_id),
        after_state={"new_device_key_fingerprint": new_key.fingerprint},
    )
    return new_key
