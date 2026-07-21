"""Server signing-key management (Part I, ADR-6.1/6.6).

Private key material NEVER touches a database column or the Owner UI. Each
key's private half lives as a PEM file at
OWNER_SIGNING_KEY_DIRECTORY/<key_id>.pem, outside the repository (gitignored),
referenced only by key_id (never a stored path -- the path is always
deterministically recomputed from key_id + the configured directory, so no DB
row can ever point somewhere unexpected).
"""
from __future__ import annotations

import base64
import os
import re
import uuid
from datetime import datetime, timezone

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from sqlalchemy import select

from app.audit.services import record as audit_record
from app.extensions import db_session
from app.models.licensing_service import KeyRotationEvent, SigningKey

_KEY_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class SigningKeyError(RuntimeError):
    pass


def _safe_key_path(key_directory: str, key_id: str) -> str:
    """Rejects anything that isn't a plain filename-safe token -- no path
    traversal via key_id, ever."""
    if not _KEY_ID_RE.match(key_id):
        raise SigningKeyError(f"Refusing unsafe signing key id: {key_id!r}")
    directory = os.path.abspath(key_directory)
    path = os.path.abspath(os.path.join(directory, f"{key_id}.pem"))
    if os.path.commonpath([directory, path]) != directory:
        raise SigningKeyError("Resolved signing key path escapes the configured signing key directory.")
    return path


def _new_key_id() -> str:
    return f"owner-ed25519-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"


def generate_signing_key(key_directory: str, actor_staff_user_id=None) -> SigningKey:
    """Creates a new key pair in DRAFT status. Does not activate it -- callers
    must explicitly activate_signing_key() or rotate_signing_key()."""
    os.makedirs(key_directory, exist_ok=True)
    private_key = Ed25519PrivateKey.generate()
    key_id = _new_key_id()
    path = _safe_key_path(key_directory, key_id)

    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(pem)

    public_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )
    row = SigningKey(
        key_id=key_id, algorithm="ed25519", public_key=base64.b64encode(public_bytes).decode("ascii"), status="DRAFT"
    )
    db_session.add(row)
    db_session.commit()
    audit_record(
        actor_staff_user_id=actor_staff_user_id, actor_role_snapshot=None, action_code="SIGNING_KEY_GENERATED",
        entity_type="signing_key", entity_public_id=key_id, after_state={"key_id": key_id, "algorithm": "ed25519"},
    )
    return row


def activate_signing_key(key_directory: str, key_id: str, actor_staff_user_id=None) -> SigningKey:
    row = db_session.execute(select(SigningKey).where(SigningKey.key_id == key_id)).scalars().first()
    if row is None:
        raise SigningKeyError(f"No such signing key: {key_id}")
    if not os.path.exists(_safe_key_path(key_directory, key_id)):
        raise SigningKeyError(f"Private key file missing for {key_id} -- refusing to activate.")

    now = datetime.now(timezone.utc)
    previously_active = db_session.execute(select(SigningKey).where(SigningKey.status == "ACTIVE")).scalars().first()
    if previously_active is not None and previously_active.key_id != key_id:
        previously_active.status = "RETIRED"
        previously_active.retired_at = now

    row.status = "ACTIVE"
    row.activated_at = now
    db_session.add(
        KeyRotationEvent(
            from_key_id=previously_active.key_id if previously_active else None,
            to_key_id=key_id,
            rotated_by_staff_user_id=actor_staff_user_id,
            reason="manual_activation",
        )
    )
    db_session.commit()
    audit_record(
        actor_staff_user_id=actor_staff_user_id, actor_role_snapshot=None, action_code="SIGNING_KEY_ACTIVATED",
        entity_type="signing_key", entity_public_id=key_id,
        before_state={"previously_active_key_id": previously_active.key_id if previously_active else None},
        after_state={"active_key_id": key_id},
    )
    return row


def rotate_signing_key(key_directory: str, reason: str, actor_staff_user_id=None) -> SigningKey:
    """Generates a fresh key and activates it in one step -- the standard
    rotation operation. The previous active key moves to RETIRED, not
    REVOKED, so assertions it already signed remain verifiable (Part I's
    overlap requirement)."""
    new_key = generate_signing_key(key_directory, actor_staff_user_id)
    activate_signing_key(key_directory, new_key.key_id, actor_staff_user_id)
    row = db_session.execute(select(SigningKey).where(SigningKey.key_id == new_key.key_id)).scalars().first()
    audit_record(
        actor_staff_user_id=actor_staff_user_id, actor_role_snapshot=None, action_code="SIGNING_KEY_ROTATED",
        entity_type="signing_key", entity_public_id=new_key.key_id, reason=reason,
    )
    return row


def revoke_signing_key(key_id: str, reason: str, actor_staff_user_id=None) -> SigningKey:
    """For compromise response -- unlike retirement, a revoked key's
    signature must NEVER be trusted again, even for an assertion issued
    before revocation (Part J's 'revoked key behavior' test)."""
    row = db_session.execute(select(SigningKey).where(SigningKey.key_id == key_id)).scalars().first()
    if row is None:
        raise SigningKeyError(f"No such signing key: {key_id}")
    row.status = "REVOKED"
    row.revoked_at = datetime.now(timezone.utc)
    row.revocation_reason = reason
    db_session.commit()
    audit_record(
        actor_staff_user_id=actor_staff_user_id, actor_role_snapshot=None, action_code="SIGNING_KEY_REVOKED",
        entity_type="signing_key", entity_public_id=key_id, reason=reason,
    )
    return row


def retire_signing_key(key_id: str, actor_staff_user_id=None) -> SigningKey:
    row = db_session.execute(select(SigningKey).where(SigningKey.key_id == key_id)).scalars().first()
    if row is None:
        raise SigningKeyError(f"No such signing key: {key_id}")
    row.status = "RETIRED"
    row.retired_at = datetime.now(timezone.utc)
    db_session.commit()
    audit_record(
        actor_staff_user_id=actor_staff_user_id, actor_role_snapshot=None, action_code="SIGNING_KEY_RETIRED",
        entity_type="signing_key", entity_public_id=key_id,
    )
    return row


def get_active_signing_key() -> SigningKey | None:
    return db_session.execute(select(SigningKey).where(SigningKey.status == "ACTIVE")).scalars().first()


def load_private_key(key_directory: str, key_id: str) -> Ed25519PrivateKey:
    path = _safe_key_path(key_directory, key_id)
    if not os.path.exists(path):
        raise SigningKeyError(f"Private key file missing for {key_id}.")
    with open(path, "rb") as fh:
        pem = fh.read()
    return serialization.load_pem_private_key(pem, password=None)


def load_public_key(base64_public_key: str) -> Ed25519PublicKey:
    return Ed25519PublicKey.from_public_bytes(base64.b64decode(base64_public_key))


def export_public_keys() -> list[dict]:
    """Every key ever published (ACTIVE/RETIRED/REVOKED) EXCEPT bare DRAFT
    (never-activated, never used to sign anything, no reason to publish) --
    a verifier must be able to check an old assertion against a RETIRED key,
    and must be told a REVOKED key is no longer trustworthy rather than
    having it silently vanish (Part J)."""
    rows = db_session.execute(
        select(SigningKey).where(SigningKey.status.in_(["ACTIVE", "RETIRED", "REVOKED"])).order_by(SigningKey.created_at.desc())
    ).scalars().all()
    return [
        {
            "key_id": r.key_id,
            "algorithm": r.algorithm,
            "public_key": r.public_key,
            "use": "assertion-signing",
            "status": r.status,
            "valid_from": r.activated_at.isoformat() if r.activated_at else None,
            "retired_at": r.retired_at.isoformat() if r.retired_at else None,
        }
        for r in rows
    ]


def verify_signing_key_health(key_directory: str) -> dict:
    active = get_active_signing_key()
    if active is None:
        return {"status": "FAILED", "detail": "No ACTIVE signing key."}
    try:
        private_key = load_private_key(key_directory, active.key_id)
    except SigningKeyError as exc:
        return {"status": "FAILED", "detail": str(exc)}
    test_payload = b"owner-signing-key-health-check"
    signature = private_key.sign(test_payload)
    public_key = private_key.public_key()
    try:
        public_key.verify(signature, test_payload)
    except Exception as exc:  # pragma: no cover -- defensive, cryptography raises InvalidSignature
        return {"status": "FAILED", "detail": f"Self-verification failed: {exc}"}
    published = base64.b64encode(
        public_key.public_bytes(encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)
    ).decode("ascii")
    if published != active.public_key:
        return {"status": "FAILED", "detail": "Private key on disk does not match the published public key."}
    return {"status": "OK", "detail": f"Active key {active.key_id} sign/verify round-trip succeeded."}
