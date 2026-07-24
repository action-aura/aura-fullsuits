"""Initial activation orchestration (Part G/I) -- the counterpart to
checkin_scheduler.py's run_once() for an installation that has no stored
license state yet. Ties LicensingClient, AssertionVerifier,
LicenseStateRepository, and LicensingEventRecorder together for exactly one
flow: submit a license key, verify what comes back, persist the first
record.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from .assertion_verifier import AssertionVerificationError, verify_assertion
from .client import LicensingClient, LicensingClientError
from .events import LicensingEventRecorder
from .state_machine import LicenseState
from .state_repository import LICENSING_SCHEMA_VERSION, LicenseStateRecord, LicenseStateRepository
from .trusted_time import cache_fresh_anchor
from .trust_store import OwnerTrustStore


class ActivationFailed(Exception):
    """Raised with a LOCAL_REASON_CODES/PUBLIC_REASON_CODES value as
    reason_code -- the caller (UI layer) maps this to one of Part G's
    screens (Invalid License, Product Mismatch, Device Limit Reached,
    Network Unavailable, Owner Service Temporarily Unavailable, ...)."""

    def __init__(self, reason_code: str, message: str):
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class ActivationResult:
    state: LicenseState
    owner_installation_id: str


def perform_activation(
    *,
    client: LicensingClient,
    signer,
    trust_store: OwnerTrustStore,
    state_repository: LicenseStateRepository,
    event_recorder: LicensingEventRecorder,
    product_code: str,
    platform: str,
    app_version: str,
    release_channel: Optional[str],
    license_key: str,
    device_public_key_fingerprint: str,
) -> ActivationResult:
    """Windows path: this process owns the device key, so it also owns the
    HTTP call. Calls Owner directly, then delegates to
    ingest_activation_response() for verification and persistence -- the
    exact same code Android's sync path uses for that half, so the two
    platforms can never verify differently."""
    event_recorder.record("ACTIVATION_STARTED")

    client_side_installation_id = str(uuid.uuid4())
    idempotency_key = str(uuid.uuid4())

    try:
        response = client.activate(
            product_code=product_code,
            platform=platform,
            app_version=app_version,
            release_channel=release_channel,
            installation_id=client_side_installation_id,
            device_public_key_b64=signer.get_public_key_b64(),
            license_key=license_key,
            idempotency_key=idempotency_key,
            signer=signer,
        )
    except LicensingClientError as exc:
        event_recorder.record("ACTIVATION_FAILED", {"reason_code": exc.reason_code})
        raise ActivationFailed(exc.reason_code, str(exc)) from exc
    finally:
        license_key = None  # noqa: F841 -- discard the local reference; caller owns clearing its own copy

    return ingest_activation_response(
        response,
        trust_store=trust_store,
        state_repository=state_repository,
        event_recorder=event_recorder,
        product_code=product_code,
        platform=platform,
        device_public_key_fingerprint=device_public_key_fingerprint,
    )


def ingest_activation_response(
    response: dict,
    *,
    trust_store: OwnerTrustStore,
    state_repository: LicenseStateRepository,
    event_recorder: LicensingEventRecorder,
    product_code: str,
    platform: str,
    device_public_key_fingerprint: str,
) -> ActivationResult:
    """Android path (Part U): the Kotlin layer already made the signed HTTP
    call to Owner (it holds the AndroidKeystore-wrapped device key, this
    process does not) and hands the RAW, UNTRUSTED response here over the
    localhost sync endpoint. This function independently re-verifies it from
    scratch -- exactly as if this process had made the call itself -- and
    only then persists. Nothing about the Kotlin layer's own opinion of
    whether activation succeeded is trusted; the verification below is the
    only thing that grants ACTIVE_ONLINE.
    """
    if response.get("result") != "SUCCESS":
        reason_code = response.get("reason_code", "ACTIVATION_REJECTED")
        event_recorder.record("ACTIVATION_FAILED", {"reason_code": reason_code})
        raise ActivationFailed(reason_code, "Owner rejected the activation request.")

    # Server-assigned installation_id -- NOT an echo of the client's
    # self-generated installation_id (activation-protocol-v1.md).
    owner_installation_id = response.get("installation_id")
    envelope = response.get("signed_assertion")
    if not owner_installation_id or not envelope:
        event_recorder.record("ACTIVATION_FAILED", {"reason_code": "MALFORMED_RESPONSE"})
        raise ActivationFailed("MALFORMED_RESPONSE", "Owner response is missing installation_id or signed_assertion.")

    try:
        verified = verify_assertion(
            envelope,
            trust_store=trust_store,
            expected_product_code=product_code,
            expected_platform=platform,
            expected_installation_id=owner_installation_id,
            expected_device_key_fingerprint=device_public_key_fingerprint,
            trusted_now=datetime.now(timezone.utc),
        )
    except AssertionVerificationError as exc:
        # A "successful" activation whose assertion doesn't verify is not
        # trusted -- never activate on an unverifiable response, regardless
        # of what result/reason_code the envelope claimed.
        event_recorder.record("ACTIVATION_FAILED", {"reason_code": exc.reason_code})
        raise ActivationFailed(exc.reason_code, str(exc)) from exc

    payload = verified.payload
    record = LicenseStateRecord(
        licensing_schema_version=LICENSING_SCHEMA_VERSION,
        product_code=product_code,
        platform=platform,
        current_state=LicenseState.ACTIVE_ONLINE.value,
        owner_installation_id=owner_installation_id,
        device_public_key_fingerprint=device_public_key_fingerprint,
        assertion_envelope_json=json.dumps(envelope),
        assertion_id=payload.get("assertion_id"),
        assertion_issued_at=payload.get("issued_at"),
        assertion_not_before=payload.get("not_before"),
        assertion_expires_at=payload.get("expires_at"),
        trusted_time_anchor_server_time=payload.get("issued_at"),
        last_successful_checkin_at=datetime.now(timezone.utc).isoformat(),
        last_sync_result="SUCCESS",
        last_public_reason_code=response.get("reason_code"),
        license_status=verified.evidence.license_status,
        installation_status=verified.evidence.installation_status,
        subscription_status=verified.evidence.subscription_status,
        entitlements_json=json.dumps(payload.get("entitlements", {})),
        offline_policy_json=json.dumps(payload.get("offline_policy", {})),
    )
    state_repository.save(record)

    # Phase 7V-F: pin the trusted-time anchor synchronously, right here --
    # see checkin_scheduler.py's _persist_fresh_assertion for the full
    # rationale (this is the activation-time counterpart of that fix).
    cache_fresh_anchor(owner_installation_id, datetime.fromisoformat(record.trusted_time_anchor_server_time))

    event_recorder.record("ACTIVATION_SUCCEEDED")
    event_recorder.record("ASSERTION_ACCEPTED")

    return ActivationResult(state=LicenseState.ACTIVE_ONLINE, owner_installation_id=owner_installation_id)
