"""Device-initiated deactivation (Part B's POST /api/licensing/v1/deactivations,
Part N's idempotency requirement for this operation)."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from app.audit.services import record as audit_record
from app.extensions import db_session
from app.licensing_service import device_identity, idempotency, replay
from app.licensing_service.canonical import canonicalize, canonicalize_bytes
from app.models.installations import ActivationEvent, Installation
from app.models.licensing_service import ActivationRequest

SUPPORTED_CONTRACT_VERSIONS = ("v1",)
REQUIRED_FIELDS = ("contract_version", "request_id", "correlation_id", "timestamp", "nonce", "installation_id", "idempotency_key", "signature")


class DeactivationRejected(Exception):
    def __init__(self, internal_reason_code: str, http_status: int = 400):
        self.internal_reason_code = internal_reason_code
        self.http_status = http_status
        super().__init__(internal_reason_code)


def _validate_shape(body: dict) -> None:
    if not isinstance(body, dict):
        raise DeactivationRejected("INVALID_REQUEST")
    for field in REQUIRED_FIELDS:
        if field not in body or body[field] in (None, ""):
            raise DeactivationRejected("INVALID_REQUEST")
    if body["contract_version"] not in SUPPORTED_CONTRACT_VERSIONS:
        raise DeactivationRejected("UNSUPPORTED_CONTRACT_VERSION")


def process_deactivation(body: dict, *, source_ip: str | None, config: dict) -> dict:
    _validate_shape(body)
    try:
        installation_id = uuid.UUID(str(body["installation_id"]))
    except (ValueError, TypeError):
        raise DeactivationRejected("INSTALLATION_NOT_FOUND")

    try:
        request_timestamp = datetime.fromisoformat(body["timestamp"])
    except (ValueError, TypeError):
        raise DeactivationRejected("INVALID_TIMESTAMP")
    try:
        replay.validate_timestamp(request_timestamp, config["timestamp_skew_seconds"])
    except replay.ReplayError as exc:
        raise DeactivationRejected(str(exc))
    try:
        replay.consume_nonce(body["nonce"], scope="deactivation", ttl_seconds=config["nonce_ttl_seconds"])
    except replay.ReplayError as exc:
        raise DeactivationRejected(str(exc))

    installation = db_session.get(Installation, installation_id)
    if installation is None:
        raise DeactivationRejected("INSTALLATION_NOT_FOUND")

    # Verify against the MOST RECENT device key regardless of status -- an
    # idempotent retry of the very deactivation request that revoked the key
    # must still verify against that (now-revoked) key. A genuinely different
    # (never-registered-here) key still fails signature verification either way.
    device_key = device_identity.get_most_recent_device_key(installation.id)
    if device_key is None:
        raise DeactivationRejected("DEVICE_KEY_REVOKED")
    public_key = device_identity.load_public_key_or_none(device_key.public_key)
    signable = {k: v for k, v in body.items() if k != "signature"}
    if public_key is None or not device_identity.verify_signature(public_key, canonicalize_bytes(signable), body["signature"]):
        raise DeactivationRejected("INVALID_SIGNATURE")

    fingerprint = canonicalize({"installation_id": str(installation_id)})
    existing = idempotency.check_idempotency(body["idempotency_key"], "DEACTIVATION", fingerprint)
    if existing is not None and existing.cached_response_json:
        return json.loads(existing.cached_response_json)

    if installation.status != "DEACTIVATED":
        installation.status = "DEACTIVATED"
        installation.deactivated_at = datetime.now(timezone.utc)
    if device_key.status != "REVOKED":
        device_key.status = "REVOKED"
        device_key.revoked_at = datetime.now(timezone.utc)

    db_session.add(
        ActivationEvent(
            license_id=installation.license_id, installation_id=installation.id, event_type="DEACTIVATION_ACCEPTED",
            result="SUCCESS", correlation_id=body.get("correlation_id"),
        )
    )
    db_session.add(
        ActivationRequest(
            request_id=body["request_id"], correlation_id=body.get("correlation_id"), event_type="DEACTIVATION",
            license_id=installation.license_id, installation_id=installation.id, device_key_fingerprint=device_key.fingerprint,
            source_ip=source_ip, result="ACCEPTED", reason_code="DEACTIVATION_ACCEPTED",
        )
    )

    response = {
        "contract_version": body["contract_version"],
        "response_id": str(uuid.uuid4()),
        "correlation_id": body.get("correlation_id"),
        "server_timestamp": datetime.now(timezone.utc).isoformat(),
        "result": "SUCCESS",
        "reason_code": "DEACTIVATION_ACCEPTED",
        "decision": "APPROVED",
    }
    idempotency.record_idempotency(body["idempotency_key"], "DEACTIVATION", fingerprint, str(installation.id), "SUCCESS", json.dumps(response))
    db_session.commit()

    audit_record(
        actor_staff_user_id=None, actor_role_snapshot=None, action_code="DEACTIVATION_ACCEPTED",
        entity_type="installation", entity_public_id=str(installation.id), correlation_id=body.get("correlation_id"),
    )
    return response
