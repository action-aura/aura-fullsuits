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
from typing import Callable, Optional

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


class ActivationPending(Exception):
    """Phase 8 Part O/U: Owner accepted the request but is holding it for
    manual approval/risk review (`result: "PENDING"` -- see
    owner/contracts/activation-response-v1.schema.json's third branch).
    Deliberately NOT a subclass of ActivationFailed -- this is not a
    failure, and a caller that only catches ActivationFailed must not
    accidentally treat "awaiting a human decision" as "rejected." The UI
    layer should show a distinct "activation awaiting approval" screen and
    may retry later (`retry_guidance` on the Owner response is always
    "safe_to_retry_with_backoff" for this branch)."""

    def __init__(self, reason_code: str, installation_id: Optional[str], message: str):
        super().__init__(message)
        self.reason_code = reason_code
        self.installation_id = installation_id


@dataclass(frozen=True)
class ActivationResult:
    state: LicenseState
    owner_installation_id: str


def _failure_details(reason_code: str, envelope, trust_store: OwnerTrustStore) -> dict:
    """Event details for a failed activation.

    UNKNOWN_SIGNING_KEY is the one failure whose cause is invisible from the
    reason code alone, and it cost a multi-session investigation on a real
    handset (2026-09-04, Mi Note 10) to answer a question two key ids would
    have settled at a glance: Owner had rotated onto a key this install had
    never trusted, because trust_store.json is seeded ONCE and thereafter
    shadows every corrected trust_anchor.json shipped in every later build.
    Verifying the bundled anchor -- the obvious check, and the one that was
    made -- proves nothing about what the device actually trusts.

    So record both sides whenever they disagree. Key ids only: they are public
    identifiers Owner already puts in the clear in every envelope it signs, so
    this leaks nothing into a log that Part W keeps strictly local anyway.
    Every other reason code already explains itself and gets the bare code,
    unchanged.
    """
    details: dict = {"reason_code": reason_code}
    if reason_code == "UNKNOWN_SIGNING_KEY":
        details["assertion_signing_key_id"] = envelope.get("signing_key_id") if isinstance(envelope, dict) else None
        details["trusted_key_ids"] = trust_store.trusted_key_ids()
    return details


def make_trust_manifest_refresher(client: LicensingClient, trust_store: OwnerTrustStore) -> Callable[[], None]:
    """Best-effort "catch this install's trust store up with Owner" step, for
    the one case activation could not previously survive: a BUNDLED anchor
    that predates Owner's current signing key.

    The check-in path has always done this (LicenseCheckInScheduler.run_once
    refreshes the manifest before every cycle), but activation verified once
    and gave up -- so a freshly-installed build whose trust_anchor.json was
    cut before a rotation failed activation with UNKNOWN_SIGNING_KEY and had
    no way forward at all. That exact stale-anchor situation has already been
    hit on the live droplet.

    This grants no new authority: it only fetches the public key-set manifest
    and hands it to admit_manifest(), which still refuses anything not
    vouched for by a key this install already trusts. Errors are swallowed --
    a failed refresh must leave the original verification failure as the
    reported outcome, never mask it with a transport error.
    """

    def _refresh() -> None:
        try:
            trust_store.admit_manifest(client.fetch_signing_keys())
        except Exception:
            return  # best effort only -- never let this become the failure the caller sees

    return _refresh


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
        trust_refresher=make_trust_manifest_refresher(client, trust_store),
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
    trust_refresher: Optional[Callable[[], None]] = None,
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
    if response.get("result") == "PENDING":
        # Not a failure -- no assertion exists yet to persist or verify.
        # Nothing about local state changes; the next activation retry
        # (same idempotency_key, Owner-side self-healing -- see Phase 8
        # Milestone 5's design doc) is what eventually resolves this.
        reason_code = response.get("reason_code", "ACTIVATION_PENDING_REVIEW")
        installation_id = response.get("installation_id")
        event_recorder.record("ACTIVATION_PENDING", {"reason_code": reason_code})
        raise ActivationPending(reason_code, installation_id, "Activation is awaiting manual review.")

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

    def _verify():
        return verify_assertion(
            envelope,
            trust_store=trust_store,
            expected_product_code=product_code,
            expected_platform=platform,
            expected_installation_id=owner_installation_id,
            expected_device_key_fingerprint=device_public_key_fingerprint,
            trusted_now=datetime.now(timezone.utc),
        )

    try:
        verified = _verify()
    except AssertionVerificationError as exc:
        # UNKNOWN_SIGNING_KEY is the one verification failure that can be a
        # stale-trust-store problem rather than a bad assertion: Owner may
        # have rotated its signing key since this build's trust_anchor.json
        # was cut. Refresh the key-set manifest once and re-verify, exactly
        # as the check-in path already does before every cycle. Every other
        # reason_code means the assertion itself is wrong, and refreshing
        # trust could not possibly change that -- fail immediately.
        if exc.reason_code != "UNKNOWN_SIGNING_KEY" or trust_refresher is None:
            # Reached with UNKNOWN_SIGNING_KEY whenever no refresher was
            # supplied, so the diagnostic matters on this branch too.
            # A "successful" activation whose assertion doesn't verify is
            # not trusted -- never activate on an unverifiable response,
            # regardless of what result/reason_code the envelope claimed.
            event_recorder.record("ACTIVATION_FAILED", _failure_details(exc.reason_code, envelope, trust_store))
            raise ActivationFailed(exc.reason_code, str(exc)) from exc

        trust_refresher()
        try:
            # Exactly one retry. The refresh either produced a trusted
            # signer or it did not; retrying further would just repeat the
            # same deterministic verification against the same trust store.
            verified = _verify()
        except AssertionVerificationError as retry_exc:
            # After the refresh: if this is STILL UNKNOWN_SIGNING_KEY, the
            # manifest could not vouch for Owner's current key with anything
            # this install trusts -- the unrecoverable case. trusted_key_ids()
            # is re-read here, post-refresh, so it reflects what the store
            # actually ended up with rather than what it started as.
            event_recorder.record("ACTIVATION_FAILED", _failure_details(retry_exc.reason_code, envelope, trust_store))
            raise ActivationFailed(retry_exc.reason_code, str(retry_exc)) from retry_exc

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
        # Launch-readiness (2026-09-03): stored exactly like
        # owner_installation_id above -- read straight off the raw
        # activation `response`, not the signed assertion `payload`. Owner
        # only ever puts this on the outer response when
        # OWNER_SYNC_RELAY_PUBLIC_URL is configured for that deploy
        # (routes.py/_service_config()); .get() returns None when the key
        # is absent, matching the field's Optional[str] = None default.
        # Deliberately UNVALIDATED here -- this repository only persists
        # what Owner said. The one place this value is ever actually
        # trusted enough to use is products/*/backend/config.py, which runs
        # it through the exact same validate_sync_relay_url() a typed env
        # var gets before ever handing it to the sync loop.
        sync_relay_base_url=response.get("sync_relay_base_url"),
    )
    state_repository.save(record)

    # Phase 7V-F: pin the trusted-time anchor synchronously, right here --
    # see checkin_scheduler.py's _persist_fresh_assertion for the full
    # rationale (this is the activation-time counterpart of that fix).
    cache_fresh_anchor(owner_installation_id, datetime.fromisoformat(record.trusted_time_anchor_server_time))

    event_recorder.record("ACTIVATION_SUCCEEDED")
    event_recorder.record("ASSERTION_ACCEPTED")

    return ActivationResult(state=LicenseState.ACTIVE_ONLINE, owner_installation_id=owner_installation_id)
