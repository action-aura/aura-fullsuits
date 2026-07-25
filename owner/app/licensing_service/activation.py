"""Initial activation protocol (Part E), device-limit-safe (Part F).

process_activation() implements the exact 23-step sequence from the governing
spec's Part E, in order. Every plaintext-license-key-handling rule from that
part is enforced structurally: the submitted key exists only as a local
variable for the duration of this function call, is never assigned to
anything that outlives it, never logged, never placed in an exception
message, and never returned.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone

from sqlalchemy import select

from app.audit.services import record as audit_record
from app.extensions import db_session
from app.licensing_service import device_identity, idempotency, replay
from app.licensing_service.assertions import build_assertion_payload, persist_assertion, sign_assertion
from app.licensing_service.canonical import canonicalize, canonicalize_bytes
from app.licensing_service.entitlements import resolve_entitlements
from app.licensing_service.offline_policy import get_policy_for_license, serialize_policy
from app.models.catalog import Platform, Product, ReleaseChannel
from app.models.installations import ActivationEvent, Installation
from app.models.licensing import License
from app.models.licensing_service import ActivationRequest
from app.security.license_keys import hash_license_secret

SUPPORTED_CONTRACT_VERSIONS = ("v1",)
_SLOT_CONSUMING_STATUSES = ("REGISTERED", "PENDING_ACTIVATION", "ACTIVE", "SUSPENDED")


class ActivationRejected(Exception):
    def __init__(self, internal_reason_code: str, http_status: int = 400):
        self.internal_reason_code = internal_reason_code
        self.http_status = http_status
        super().__init__(internal_reason_code)


REQUIRED_FIELDS = (
    "contract_version", "request_id", "correlation_id", "timestamp", "nonce", "product_code", "platform",
    "app_version", "installation_id", "device_public_key", "device_public_key_algorithm", "license_key",
    "idempotency_key", "signature",
)


def _validate_shape(body: dict) -> None:
    if not isinstance(body, dict):
        raise ActivationRejected("INVALID_REQUEST")
    for field in REQUIRED_FIELDS:
        if field not in body or body[field] in (None, ""):
            raise ActivationRejected("INVALID_REQUEST")
    if body["contract_version"] not in SUPPORTED_CONTRACT_VERSIONS:
        raise ActivationRejected("UNSUPPORTED_CONTRACT_VERSION")


def _signable_fields(body: dict) -> dict:
    """Every field except the signature itself is part of the signed payload."""
    return {k: v for k, v in body.items() if k != "signature"}


def _safe_request_fingerprint(body: dict) -> str:
    """A stable fingerprint of the request's non-secret, non-nonce-varying
    fields, used only to detect an idempotency-key reused for a materially
    different request (Part N) -- deliberately excludes the license key,
    nonce, timestamp, request_id, and signature."""
    stable = {
        k: v for k, v in body.items()
        if k in ("product_code", "platform", "app_version", "release_channel", "installation_id", "device_public_key")
    }
    return canonicalize(stable)


def process_activation(body: dict, *, source_ip: str | None, config: dict) -> dict:
    # Steps 1-2: shape + timestamp
    _validate_shape(body)
    try:
        request_timestamp = datetime.fromisoformat(body["timestamp"])
    except (ValueError, TypeError):
        raise ActivationRejected("INVALID_TIMESTAMP")
    try:
        replay.validate_timestamp(request_timestamp, config["timestamp_skew_seconds"])
    except replay.ReplayError as exc:
        raise ActivationRejected(str(exc))

    # Step 3: nonce
    try:
        replay.consume_nonce(body["nonce"], scope="activation", ttl_seconds=config["nonce_ttl_seconds"])
    except replay.ReplayError as exc:
        raise ActivationRejected(str(exc))

    # Step 4: device signature (proves possession of the SUBMITTED public key)
    try:
        public_key = device_identity.validate_public_key(body["device_public_key_algorithm"], body["device_public_key"])
    except device_identity.DeviceIdentityError as exc:
        raise ActivationRejected(str(exc))
    canonical_bytes = canonicalize_bytes(_signable_fields(body))
    if not device_identity.verify_signature(public_key, canonical_bytes, body["signature"]):
        _log_rejected_request(body, source_ip, "INVALID_SIGNATURE")
        raise ActivationRejected("INVALID_SIGNATURE")

    # Steps 5-8: normalize, HMAC, constant-time compare, locate -- the
    # plaintext key lives ONLY in this local variable, for exactly these lines.
    submitted_key = body["license_key"].strip().upper()
    computed_hmac = hash_license_secret(submitted_key, config["license_pepper"])
    license_row = db_session.execute(select(License).where(License.key_secret_hmac == computed_hmac)).scalars().first()
    submitted_key = None  # noqa: F841 -- explicitly drop the reference the instant it's no longer needed
    del computed_hmac
    if license_row is None:
        _log_rejected_request(body, source_ip, "LICENSE_NOT_FOUND")
        raise ActivationRejected("LICENSE_NOT_FOUND")

    # Steps 9-11: product / platform / release channel
    product = db_session.execute(select(Product).where(Product.product_code == body["product_code"])).scalars().first()
    if product is None or product.id != license_row.product_id:
        raise ActivationRejected("PRODUCT_MISMATCH")
    platform = db_session.execute(select(Platform).where(Platform.platform_code == body["platform"])).scalars().first()
    if platform is None or body["platform"] not in (license_row.allowed_platforms or "").split(","):
        raise ActivationRejected("PLATFORM_NOT_ALLOWED")
    release_channel_code = body.get("release_channel")
    if release_channel_code and license_row.allowed_release_channel_id:
        channel = db_session.execute(select(ReleaseChannel).where(ReleaseChannel.channel_code == release_channel_code)).scalars().first()
        if channel is None or channel.id != license_row.allowed_release_channel_id:
            raise ActivationRejected("RELEASE_CHANNEL_NOT_ALLOWED")

    # Step 12: license status
    if license_row.status in ("DRAFT",):
        raise ActivationRejected("LICENSE_NOT_ISSUED")
    if license_row.status == "SUSPENDED":
        raise ActivationRejected("LICENSE_SUSPENDED")
    if license_row.status == "REVOKED":
        raise ActivationRejected("LICENSE_REVOKED")
    if license_row.status == "REPLACED":
        raise ActivationRejected("LICENSE_REPLACED")
    if license_row.status == "EXPIRED":
        raise ActivationRejected("LICENSE_EXPIRED")
    if license_row.status not in ("ISSUED", "ACTIVE"):
        raise ActivationRejected("LICENSE_NOT_ACTIVE")

    # Step 13: subscription state
    subscription = license_row.subscription
    if subscription.status in ("CANCELLED",):
        raise ActivationRejected("SUBSCRIPTION_CANCELLED")
    if subscription.status in ("EXPIRED",):
        raise ActivationRejected("SUBSCRIPTION_EXPIRED")
    if subscription.status in ("SUSPENDED",):
        raise ActivationRejected("SUBSCRIPTION_SUSPENDED")
    if subscription.status not in ("ACTIVE", "PILOT"):
        raise ActivationRejected("SUBSCRIPTION_INACTIVE")

    # Step 14: validity dates
    today = date.today()
    if license_row.valid_from and today < license_row.valid_from:
        raise ActivationRejected("LICENSE_NOT_YET_VALID")
    if license_row.valid_until and today > license_row.valid_until:
        raise ActivationRejected("LICENSE_EXPIRED")

    # Step 15 (soft pre-check; the authoritative check is step 17, under lock)
    # -- deliberately not raising here, the locked recheck is what counts.

    # Step 16: idempotency
    fingerprint = _safe_request_fingerprint(body)
    try:
        existing = idempotency.check_idempotency(body["idempotency_key"], "ACTIVATION", fingerprint)
    except idempotency.IdempotencyConflictError:
        raise ActivationRejected("IDEMPOTENCY_CONFLICT", http_status=409)
    if existing is not None and existing.cached_response_json:
        return json.loads(existing.cached_response_json)

    # Steps 17-18: lock the license row, authoritative device-limit check,
    # register-or-reuse the installation -- all inside one transaction.
    locked_license = db_session.execute(
        select(License).where(License.id == license_row.id).with_for_update()
    ).scalars().first()

    existing_installation = db_session.execute(
        select(Installation).where(
            Installation.license_id == locked_license.id, Installation.installation_label == body["installation_id"]
        )
    ).scalars().first()

    if existing_installation is None:
        # The client-generated installation_id is fresh on every activation
        # attempt by protocol design (activation-protocol-v1.md), so it alone
        # cannot detect a retry of a request whose SUCCESS response the
        # client never received (a real, confirmed-in-production failure
        # mode over an unreliable transport -- Phase 7V-A). The device key
        # fingerprint IS stable across attempts (it is the device's
        # persistent identity) and is globally unique in this table, so use
        # it as the fallback retry signal: if this exact device is already
        # an ACTIVE installation on THIS license, this is that same device
        # retrying, not a new device -- reuse its installation rather than
        # colliding on the fingerprint UNIQUE constraint below.
        existing_device_key = device_identity.get_device_key_by_fingerprint(
            device_identity.fingerprint_of(body["device_public_key"])
        )
        if (
            existing_device_key is not None
            and existing_device_key.status == "ACTIVE"
            and existing_device_key.installation.license_id == locked_license.id
        ):
            existing_installation = existing_device_key.installation

    if existing_installation is not None:
        # Same client-generated installation_id reactivating -- reuse it, do
        # not consume an additional slot. A DIFFERENT device attempting to
        # reuse someone else's installation_id is rejected at the device-key
        # binding check.
        active_device_key = device_identity.get_active_device_key(existing_installation.id)
        if active_device_key is not None and active_device_key.fingerprint != device_identity.fingerprint_of(body["device_public_key"]):
            raise ActivationRejected("DEVICE_KEY_MISMATCH")
        installation = existing_installation
    else:
        active_count = db_session.execute(
            select(Installation).where(Installation.license_id == locked_license.id, Installation.status.in_(_SLOT_CONSUMING_STATUSES))
        ).scalars().all()
        if len(active_count) >= locked_license.device_limit:
            raise ActivationRejected("DEVICE_LIMIT_REACHED")
        installation = Installation(
            customer_id=locked_license.customer_id, subscription_id=locked_license.subscription_id,
            license_id=locked_license.id, product_id=product.id, platform_id=platform.id,
            installation_label=body["installation_id"], app_version=body["app_version"],
            status="ACTIVE", first_registered_at=datetime.now(timezone.utc),
        )
        db_session.add(installation)
        db_session.flush()
        device_identity.register_device_key(installation.id, body["device_public_key_algorithm"], body["device_public_key"])

    installation.last_check_in_at = datetime.now(timezone.utc)
    installation.activation_count += 1
    if installation.status not in ("ACTIVE",):
        installation.status = "ACTIVE"

    # Step 19: activation event
    db_session.add(
        ActivationEvent(
            license_id=locked_license.id, installation_id=installation.id, event_type="ACTIVATION_APPROVED",
            result="SUCCESS", correlation_id=body.get("correlation_id"),
        )
    )

    # Step 20: entitlements
    entitlements = resolve_entitlements(locked_license, datetime.now(timezone.utc))

    # Step 21: signed assertion
    offline_policy = get_policy_for_license(locked_license)
    device_key = device_identity.get_active_device_key(installation.id)
    payload = build_assertion_payload(
        license_row=locked_license, installation_row=installation,
        device_fingerprint=device_key.fingerprint if device_key else None,
        entitlements=entitlements, offline_policy=serialize_policy(offline_policy),
        contract_version=body["contract_version"], ttl_seconds=config["assertion_ttl_seconds"],
    )
    envelope = sign_assertion(payload, config["signing_key_directory"])
    persist_assertion(envelope, locked_license, installation)

    db_session.add(
        ActivationRequest(
            request_id=body["request_id"], correlation_id=body.get("correlation_id"), event_type="ACTIVATION",
            product_id=product.id, platform_id=platform.id, license_id=locked_license.id, installation_id=installation.id,
            device_key_fingerprint=device_identity.fingerprint_of(body["device_public_key"]), source_ip=source_ip,
            result="ACCEPTED", reason_code="ACTIVATION_APPROVED",
        )
    )

    # Step 22: safe response
    response = {
        "contract_version": body["contract_version"],
        "response_id": payload["assertion_id"],
        "correlation_id": body.get("correlation_id"),
        "server_timestamp": datetime.now(timezone.utc).isoformat(),
        "result": "SUCCESS",
        "reason_code": "ACTIVATION_APPROVED",
        "decision": "APPROVED",
        # The server-assigned public installation ID -- NOT an echo of the
        # client-generated installation_id from the request. The client must
        # persist this value and use it (not its own locally-generated ID)
        # for every subsequent check-in/deactivation (Part H's
        # 'installation_public_id').
        "installation_id": str(installation.id),
        "signed_assertion": envelope,
        "signing_key_id": envelope["signing_key_id"],
        "assertion_version": envelope["assertion_version"],
    }

    idempotency.record_idempotency(
        body["idempotency_key"], "ACTIVATION", fingerprint, str(installation.id), "SUCCESS", json.dumps(response)
    )

    # Step 23: single commit for the whole decision
    db_session.commit()

    audit_record(
        actor_staff_user_id=None, actor_role_snapshot=None, action_code="ACTIVATION_ACCEPTED",
        entity_type="installation", entity_public_id=str(installation.id),
        after_state={"license_id": str(locked_license.id), "product_code": product.product_code},
        correlation_id=body.get("correlation_id"),
    )
    return response


def _log_rejected_request(body: dict, source_ip: str | None, reason_code: str) -> None:
    db_session.add(
        ActivationRequest(
            request_id=body.get("request_id", "unknown")[:128], correlation_id=body.get("correlation_id"),
            event_type="ACTIVATION", source_ip=source_ip, result="REJECTED", reason_code=reason_code,
        )
    )
    db_session.commit()
