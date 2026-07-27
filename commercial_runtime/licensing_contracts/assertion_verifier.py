"""AssertionVerifier (Part K) -- independent, from-scratch verification of
every Owner-signed assertion. Never trusts transport, never trusts a prior
verification performed elsewhere (e.g. by the Android Kotlin layer -- see
docs/licensing/phase7/product-integration-architecture.md's authority-
boundary table).
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

# Real client devices routinely have a system clock a few seconds off from
# Owner's (timezone database staleness, imperfect NTP sync, no NTP at all on
# some Android builds) -- confirmed via physical Phase 7V-A testing, where a
# real device clock merely ~1 second behind the signing host caused every
# activation to be rejected as ASSERTION_NOT_YET_VALID despite a genuinely
# valid, freshly-issued assertion. This tolerance only widens the validity
# WINDOW boundary by a small, fixed amount on both ends -- it does not weaken
# signature verification, replay protection, or any authorization check, and
# mirrors the same clock-skew-tolerance concept Owner's own request-timestamp
# check already uses (ACTIVATION_TIMESTAMP_SKEW_SECONDS).
CLOCK_SKEW_TOLERANCE_SECONDS = 60

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .canonical import CanonicalizationError, canonicalize_bytes
from .policy_evaluator import AssertionEvidence, OfflinePolicy, PolicyEvaluationError
from .trust_store import OwnerTrustStore

# Mirrors owner/app/licensing_service/assertions.py::FORBIDDEN_ASSERTION_MARKERS
# -- a second, independent guard on the receiving side. Even though Owner's
# own guard already prevents these from being signed in the first place, a
# client that only trusted the server's guard would have no defense if that
# guard ever regressed -- this list is checked again here, structurally
# independent of Owner's code.
FORBIDDEN_ASSERTION_MARKERS = frozenset(
    {
        "license_key",
        "key_secret",
        "pepper",
        "password",
        "card_number",
        "bank_account",
        "patient",
        "medical_note",
        "clinical_note",
        "diagnosis",
        "prescription",
        "appointment",
        "invoice_total",
        "sale_total",
        "stock_quantity",
        "local_database",
    }
)

ALLOWED_PAYLOAD_FIELDS = frozenset(
    {
        "assertion_id",
        "issuer",
        "product_code",
        "license_public_id",
        "installation_public_id",
        "platform",
        "app_version_policy",
        "release_channel",
        "issued_at",
        "not_before",
        "expires_at",
        "license_status",
        "installation_status",
        "subscription_status",
        "allowed_device_count",
        "device_key_fingerprint",
        "entitlements",
        "offline_policy",
        "contract_version",
        # Phase 8 Part W (Milestone 7): commercial-state fields resolved by
        # Owner's commercial_ops/assertion_fields.py and merged into every
        # assertion payload since. Discovered missing here by Phase 8V's
        # live-wire scenario test (test_phase8v_scenario_live_server.py) --
        # without these, this allowlist rejected every assertion issued
        # after Milestone 7 shipped, which would have silently broken every
        # real activation/check-in. Milestone 7's own claim of "zero
        # client-side parsing changes required" was correct for typed
        # deserialization (neither platform types this payload) but missed
        # this package's separate, stricter allowlist gate.
        "commercial_policy_version",
        "renewal_status",
        "plan_code",
        "term_start",
        "term_end",
        "past_due_since",
        "commercial_grace_end",
        "pilot_status",
        "emergency_extension_id",
    }
)


class AssertionVerificationError(ValueError):
    def __init__(self, reason_code: str, message: str):
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class VerifiedAssertion:
    payload: dict
    evidence: AssertionEvidence
    signing_key_id: str


def _guard_payload(payload: dict) -> None:
    for key in payload.keys():
        if key not in ALLOWED_PAYLOAD_FIELDS:
            raise AssertionVerificationError(
                "ASSERTION_FORBIDDEN_FIELD", f"Assertion payload contains a field outside the allowlist: {key!r}."
            )
    flat = str(payload).lower()
    for marker in FORBIDDEN_ASSERTION_MARKERS:
        if marker in flat:
            raise AssertionVerificationError(
                "ASSERTION_FORBIDDEN_FIELD", f"Assertion payload contains a forbidden marker: {marker!r}."
            )


def verify_assertion(
    envelope: dict,
    *,
    trust_store: OwnerTrustStore,
    expected_product_code: str,
    expected_platform: str,
    expected_installation_id: str,
    expected_device_key_fingerprint: str,
    trusted_now: datetime,
) -> VerifiedAssertion:
    """Full independent verification. Raises AssertionVerificationError with
    a specific, locally-defined reason_code (see reason_codes.py's
    LOCAL_REASON_CODES) on any failure -- never returns a partially-trusted
    result."""
    try:
        payload = envelope["payload"]
        signing_key_id = envelope["signing_key_id"]
        algorithm = envelope["algorithm"]
        signature_b64 = envelope["signature"]
    except (KeyError, TypeError) as exc:
        raise AssertionVerificationError("ASSERTION_VERIFICATION_FAILED", f"Malformed envelope: {exc}") from exc

    if algorithm != "ed25519":
        raise AssertionVerificationError(
            "ASSERTION_VERIFICATION_FAILED", f"Unsupported assertion algorithm: {algorithm!r}."
        )

    _guard_payload(payload)

    if not trust_store.is_trusted(signing_key_id):
        raise AssertionVerificationError(
            "UNKNOWN_SIGNING_KEY", f"Assertion signed by an untrusted key: {signing_key_id!r}."
        )
    public_key_b64 = trust_store.get_public_key_b64(signing_key_id)

    try:
        canonical_bytes = canonicalize_bytes(payload)
        signature = base64.b64decode(signature_b64)
        public_key_raw = base64.b64decode(public_key_b64)
    except (CanonicalizationError, ValueError) as exc:
        raise AssertionVerificationError("ASSERTION_VERIFICATION_FAILED", f"Malformed assertion data: {exc}") from exc

    try:
        Ed25519PublicKey.from_public_bytes(public_key_raw).verify(signature, canonical_bytes)
    except InvalidSignature as exc:
        raise AssertionVerificationError("ASSERTION_VERIFICATION_FAILED", "Assertion signature is invalid.") from exc

    try:
        not_before = datetime.fromisoformat(payload["not_before"])
        expires_at = datetime.fromisoformat(payload["expires_at"])
    except (KeyError, ValueError) as exc:
        raise AssertionVerificationError("ASSERTION_VERIFICATION_FAILED", f"Malformed assertion dates: {exc}") from exc
    if not_before.tzinfo is None or expires_at.tzinfo is None:
        raise AssertionVerificationError("ASSERTION_VERIFICATION_FAILED", "Assertion dates must be timezone-aware.")

    tolerance = timedelta(seconds=CLOCK_SKEW_TOLERANCE_SECONDS)
    if trusted_now < not_before - tolerance:
        raise AssertionVerificationError("ASSERTION_NOT_YET_VALID", "Assertion is not yet valid.")
    if trusted_now > expires_at + tolerance:
        raise AssertionVerificationError("ASSERTION_EXPIRED", "Assertion has expired.")

    if payload.get("product_code") != expected_product_code:
        raise AssertionVerificationError("ASSERTION_PRODUCT_MISMATCH", "Assertion product_code does not match.")
    if payload.get("platform") != expected_platform:
        raise AssertionVerificationError("ASSERTION_PLATFORM_MISMATCH", "Assertion platform does not match.")
    if payload.get("installation_public_id") != expected_installation_id:
        raise AssertionVerificationError(
            "ASSERTION_INSTALLATION_MISMATCH", "Assertion installation_public_id does not match."
        )
    if payload.get("device_key_fingerprint") != expected_device_key_fingerprint:
        raise AssertionVerificationError(
            "ASSERTION_DEVICE_MISMATCH", "Assertion device_key_fingerprint does not match this device's key."
        )

    try:
        policy_raw = payload["offline_policy"]
        offline_policy = OfflinePolicy(
            check_in_interval_seconds=policy_raw["check_in_interval_seconds"],
            retry_interval_seconds=policy_raw["retry_interval_seconds"],
            offline_grace_seconds=policy_raw["offline_grace_seconds"],
            warning_start_seconds=policy_raw["warning_start_seconds"],
            hard_expiry_behavior=policy_raw["hard_expiry_behavior"],
            clock_rollback_tolerance_seconds=policy_raw["clock_rollback_tolerance_seconds"],
            assertion_refresh_threshold_seconds=policy_raw["assertion_refresh_threshold_seconds"],
            emergency_extension_allowed=policy_raw.get("emergency_extension_allowed", False),
            emergency_extension_until=(
                datetime.fromisoformat(policy_raw["emergency_extension_until"])
                if policy_raw.get("emergency_extension_until")
                else None
            ),
        )
    except (KeyError, PolicyEvaluationError) as exc:
        raise AssertionVerificationError(
            "ASSERTION_VERIFICATION_FAILED", f"Malformed or unsafe offline_policy: {exc}"
        ) from exc

    evidence = AssertionEvidence(
        not_before=not_before,
        expires_at=expires_at,
        license_status=payload.get("license_status", ""),
        installation_status=payload.get("installation_status", ""),
        subscription_status=payload.get("subscription_status", ""),
        offline_policy=offline_policy,
    )

    return VerifiedAssertion(payload=payload, evidence=evidence, signing_key_id=signing_key_id)
