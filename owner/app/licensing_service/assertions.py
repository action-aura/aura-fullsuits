"""Signed activation assertions -- issuance and verification (Part H)."""
from __future__ import annotations

import base64
import uuid
from datetime import datetime, timedelta, timezone

from cryptography.exceptions import InvalidSignature
from sqlalchemy import select

from app.extensions import db_session
from app.licensing_service.canonical import canonicalize_bytes
from app.licensing_service.signing import SigningKeyError, get_active_signing_key, load_private_key, load_public_key
from app.models.licensing_service import EntitlementSnapshot, SignedAssertion, SigningKey

ASSERTION_VERSION = 1
FORBIDDEN_ASSERTION_MARKERS = (
    "license_key", "key_secret_hmac", "pepper", "payment", "tax_id", "tax_identifier",
    "patient", "diagnosis", "prescription", "sale", "inventory",
    "card_number", "bank_account", "medical_note", "clinical_note", "appointment", "local_database",
)


class AssertionError_(ValueError):  # noqa: N818 -- avoid shadowing builtins.AssertionError
    pass


def _guard_payload(payload: dict) -> dict:
    for key in payload.keys():
        lowered = key.lower()
        if any(marker in lowered for marker in FORBIDDEN_ASSERTION_MARKERS):
            raise AssertionError_(f"Field '{key}' is forbidden in a signed assertion payload.")
    return payload


def build_assertion_payload(
    *, license_row, installation_row, device_fingerprint: str | None, entitlements: dict, offline_policy: dict,
    contract_version: str, ttl_seconds: int,
) -> dict:
    now = datetime.now(timezone.utc)
    payload = {
        "assertion_id": str(uuid.uuid4()),
        "issuer": "aura-owner",
        "product_code": license_row.product.product_code,
        "license_public_id": str(license_row.id),
        "installation_public_id": str(installation_row.id),
        "platform": installation_row.platform.platform_code if installation_row.platform else None,
        "app_version_policy": license_row.allowed_platforms,
        "release_channel": license_row.allowed_release_channel.channel_code if license_row.allowed_release_channel else None,
        "issued_at": now.isoformat(),
        "not_before": now.isoformat(),
        "expires_at": (now + timedelta(seconds=ttl_seconds)).isoformat(),
        "license_status": license_row.status,
        "installation_status": installation_row.status,
        "subscription_status": license_row.subscription.status,
        "allowed_device_count": license_row.device_limit,
        "device_key_fingerprint": device_fingerprint,
        "entitlements": entitlements,
        "offline_policy": offline_policy,
        "contract_version": contract_version,
    }
    return _guard_payload(payload)


def sign_assertion(payload: dict, key_directory: str) -> dict:
    active_key = get_active_signing_key()
    if active_key is None:
        raise SigningKeyError("SIGNING_KEY_UNAVAILABLE")
    private_key = load_private_key(key_directory, active_key.key_id)
    canonical_bytes = canonicalize_bytes(payload)
    signature = private_key.sign(canonical_bytes)
    return {
        "payload": payload,
        "signing_key_id": active_key.key_id,
        "algorithm": active_key.algorithm,
        "assertion_version": ASSERTION_VERSION,
        "signature": base64.b64encode(signature).decode("ascii"),
    }


def persist_assertion(envelope: dict, license_row, installation_row) -> SignedAssertion:
    payload = envelope["payload"]
    row = SignedAssertion(
        assertion_id=payload["assertion_id"],
        installation_id=installation_row.id,
        license_id=license_row.id,
        signing_key_id=envelope["signing_key_id"],
        assertion_version=envelope["assertion_version"],
        issued_at=datetime.fromisoformat(payload["issued_at"]),
        not_before=datetime.fromisoformat(payload["not_before"]),
        expires_at=datetime.fromisoformat(payload["expires_at"]),
    )
    db_session.add(row)
    db_session.flush()
    db_session.add(
        EntitlementSnapshot(assertion_id=row.id, entitlements=payload["entitlements"], resolved_at=datetime.now(timezone.utc))
    )
    return row


def verify_assertion(envelope: dict, *, now: datetime | None = None) -> tuple[bool, str | None]:
    """Returns (ok, reason_code_or_None). Verifies purely from the envelope +
    published signing keys -- exactly what an external client (or the
    simulator) does, no database write required to verify."""
    now = now or datetime.now(timezone.utc)
    try:
        payload = envelope["payload"]
        key_id = envelope["signing_key_id"]
        signature_b64 = envelope["signature"]
    except (KeyError, TypeError):
        return False, "INVALID_REQUEST"

    key_row = db_session.execute(select(SigningKey).where(SigningKey.key_id == key_id)).scalars().first()
    if key_row is None:
        return False, "SIGNING_KEY_UNAVAILABLE"
    if key_row.status == "REVOKED":
        return False, "INVALID_SIGNATURE"  # a revoked key's signature is never trusted, regardless of validity

    try:
        public_key = load_public_key(key_row.public_key)
        canonical_bytes = canonicalize_bytes(payload)
        signature = base64.b64decode(signature_b64, validate=True)
        public_key.verify(signature, canonical_bytes)
    except InvalidSignature:
        return False, "INVALID_SIGNATURE"
    except Exception:
        return False, "INVALID_SIGNATURE"

    try:
        not_before = datetime.fromisoformat(payload["not_before"])
        expires_at = datetime.fromisoformat(payload["expires_at"])
    except (KeyError, ValueError):
        return False, "INVALID_REQUEST"
    if now < not_before:
        return False, "INVALID_TIMESTAMP"
    if now > expires_at:
        return False, "INVALID_TIMESTAMP"

    return True, None
