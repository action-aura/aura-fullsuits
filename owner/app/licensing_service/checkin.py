"""Authenticated license check-in (Part K).

After initial activation, no request in this module ever requires the full
license key. Authentication is device-signature-based against the
PREVIOUSLY REGISTERED public key for the installation -- a stolen
installation_id without the matching private key produces INVALID_SIGNATURE,
not success.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from app.audit.services import record as audit_record
from app.extensions import db_session
from app.licensing_service import device_identity, replay
from app.licensing_service.assertions import build_assertion_payload, persist_assertion, sign_assertion
from app.licensing_service.canonical import canonicalize_bytes
from app.licensing_service.entitlements import resolve_entitlements
from app.licensing_service.offline_policy import get_policy_for_license, serialize_policy_for_subscription
from app.models.installations import ActivationEvent, Installation
from app.models.licensing_service import ActivationRequest

SUPPORTED_CONTRACT_VERSIONS = ("v1",)
REQUIRED_FIELDS = ("contract_version", "request_id", "correlation_id", "timestamp", "nonce", "installation_id", "signature")


class CheckInRejected(Exception):
    def __init__(self, internal_reason_code: str, http_status: int = 400):
        self.internal_reason_code = internal_reason_code
        self.http_status = http_status
        super().__init__(internal_reason_code)


def _validate_shape(body: dict) -> None:
    if not isinstance(body, dict):
        raise CheckInRejected("INVALID_REQUEST")
    for field in REQUIRED_FIELDS:
        if field not in body or body[field] in (None, ""):
            raise CheckInRejected("INVALID_REQUEST")
    if body["contract_version"] not in SUPPORTED_CONTRACT_VERSIONS:
        raise CheckInRejected("UNSUPPORTED_CONTRACT_VERSION")


def process_checkin(body: dict, *, source_ip: str | None, config: dict) -> dict:
    _validate_shape(body)
    try:
        installation_id = uuid.UUID(str(body["installation_id"]))
    except (ValueError, TypeError):
        raise CheckInRejected("INSTALLATION_NOT_FOUND")

    try:
        request_timestamp = datetime.fromisoformat(body["timestamp"])
    except (ValueError, TypeError):
        raise CheckInRejected("INVALID_TIMESTAMP")
    try:
        replay.validate_timestamp(request_timestamp, config["timestamp_skew_seconds"])
    except replay.ReplayError as exc:
        raise CheckInRejected(str(exc))
    try:
        replay.consume_nonce(body["nonce"], scope="checkin", ttl_seconds=config["nonce_ttl_seconds"])
    except replay.ReplayError as exc:
        raise CheckInRejected(str(exc))

    installation = db_session.get(Installation, installation_id)
    if installation is None:
        raise CheckInRejected("INSTALLATION_NOT_FOUND")

    device_key = device_identity.get_active_device_key(installation.id)
    if device_key is None:
        raise CheckInRejected("DEVICE_KEY_REVOKED")

    public_key = device_identity.load_public_key_or_none(device_key.public_key)
    signable = {k: v for k, v in body.items() if k != "signature"}
    canonical_bytes = canonicalize_bytes(signable)
    if public_key is None or not device_identity.verify_signature(public_key, canonical_bytes, body["signature"]):
        _log(body, source_ip, installation.id, "REJECTED", "INVALID_SIGNATURE")
        raise CheckInRejected("INVALID_SIGNATURE")

    if installation.status == "SUSPENDED":
        raise CheckInRejected("INSTALLATION_SUSPENDED")
    if installation.status == "DEACTIVATED":
        raise CheckInRejected("INSTALLATION_DEACTIVATED")
    if installation.status == "REPLACED":
        raise CheckInRejected("INSTALLATION_REPLACED")

    license_row = installation.license
    if license_row is None:
        raise CheckInRejected("INSTALLATION_NOT_FOUND")
    if license_row.status == "SUSPENDED":
        _issue_status_only_response(body, installation, license_row, config, "LICENSE_SUSPENDED")
        raise CheckInRejected("LICENSE_SUSPENDED")
    if license_row.status == "REVOKED":
        raise CheckInRejected("LICENSE_REVOKED")
    if license_row.status == "EXPIRED":
        raise CheckInRejected("LICENSE_EXPIRED")

    device_identity.record_successful_proof(device_key)
    installation.last_check_in_at = datetime.now(timezone.utc)
    db_session.add(
        ActivationEvent(
            license_id=license_row.id, installation_id=installation.id, event_type="CHECK_IN_RECORDED",
            result="SUCCESS", correlation_id=body.get("correlation_id"),
        )
    )

    entitlements = resolve_entitlements(license_row, datetime.now(timezone.utc))
    offline_policy = get_policy_for_license(license_row)
    # Phase 8V-P6 (Part E): functional emergency-extension wiring -- overrides
    # the emergency_extension_* fields in this assertion only when this
    # subscription has a real, active, audited extension; never mutates the
    # stored (possibly-shared) OfflinePolicy row.
    serialized_policy = serialize_policy_for_subscription(
        offline_policy, license_row.subscription_id, now=datetime.now(timezone.utc)
    )
    payload = build_assertion_payload(
        license_row=license_row, installation_row=installation, device_fingerprint=device_key.fingerprint,
        entitlements=entitlements, offline_policy=serialized_policy,
        contract_version=body["contract_version"], ttl_seconds=config["assertion_ttl_seconds"],
    )
    envelope = sign_assertion(payload, config["signing_key_directory"])
    persist_assertion(envelope, license_row, installation)

    db_session.add(
        ActivationRequest(
            request_id=body["request_id"], correlation_id=body.get("correlation_id"), event_type="CHECK_IN",
            license_id=license_row.id, installation_id=installation.id, device_key_fingerprint=device_key.fingerprint,
            source_ip=source_ip, result="ACCEPTED", reason_code="CHECK_IN_ACCEPTED",
        )
    )
    db_session.commit()

    audit_record(
        actor_staff_user_id=None, actor_role_snapshot=None, action_code="CHECK_IN_ACCEPTED",
        entity_type="installation", entity_public_id=str(installation.id), correlation_id=body.get("correlation_id"),
    )

    return {
        "contract_version": body["contract_version"],
        "response_id": payload["assertion_id"],
        "correlation_id": body.get("correlation_id"),
        "server_timestamp": datetime.now(timezone.utc).isoformat(),
        "result": "SUCCESS",
        "reason_code": "CHECK_IN_ACCEPTED",
        "decision": "APPROVED",
        "signed_assertion": envelope,
        "signing_key_id": envelope["signing_key_id"],
        "assertion_version": envelope["assertion_version"],
    }


def _issue_status_only_response(body, installation, license_row, config, reason_code) -> None:
    """Even a rejection (e.g. suspended) still records the attempt as an
    activation-event for the installation, so the admin timeline is complete."""
    db_session.add(
        ActivationEvent(
            license_id=license_row.id, installation_id=installation.id, event_type="CHECK_IN_RECORDED",
            result="FAILURE", reason_code=reason_code, correlation_id=body.get("correlation_id"),
        )
    )
    db_session.commit()


def _log(body, source_ip, installation_id, result, reason_code) -> None:
    db_session.add(
        ActivationRequest(
            request_id=body.get("request_id", "unknown")[:128], correlation_id=body.get("correlation_id"),
            event_type="CHECK_IN", installation_id=installation_id, source_ip=source_ip, result=result, reason_code=reason_code,
        )
    )
    db_session.commit()
