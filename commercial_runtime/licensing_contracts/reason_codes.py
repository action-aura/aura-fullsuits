"""Client-facing reason-code vocabulary (Part G/L).

A product only ever receives PUBLIC reason codes -- Owner normalizes every
internal-only code before it leaves the external API boundary
(owner/app/licensing_service/reason_codes.py::to_public_reason_code). This
module therefore only needs the public subset, kept in sync by hand (the
same "no import across deployables" reasoning as canonical.py) and cross-
checked against Owner's PUBLIC_REASON_CODES via the shared conformance
fixtures, not by import.
"""
from __future__ import annotations

SUCCESS_CODES = frozenset(
    {"ACTIVATION_APPROVED", "ACTIVATION_ALREADY_ACTIVE", "CHECK_IN_ACCEPTED", "DEACTIVATION_ACCEPTED"}
)

PUBLIC_REASON_CODES = frozenset(
    SUCCESS_CODES
    | {
        "INVALID_REQUEST",
        "UNSUPPORTED_CONTRACT_VERSION",
        "INVALID_TIMESTAMP",
        "TIMESTAMP_OUTSIDE_ALLOWED_WINDOW",
        "NONCE_REUSED",
        "INVALID_SIGNATURE",
        "INVALID_PUBLIC_KEY",
        "PAYLOAD_TOO_LARGE",
        "IDEMPOTENCY_CONFLICT",
        "PRODUCT_MISMATCH",
        "PLATFORM_NOT_ALLOWED",
        "RELEASE_CHANNEL_NOT_ALLOWED",
        "VERSION_NOT_ALLOWED",
        "VERSION_UNSUPPORTED",
        "DEVICE_LIMIT_REACHED",
        "INSTALLATION_NOT_FOUND",
        "INSTALLATION_SUSPENDED",
        "INSTALLATION_DEACTIVATED",
        "INSTALLATION_REPLACED",
        "DEVICE_KEY_MISMATCH",
        "DEVICE_KEY_REVOKED",
        "SERVICE_TEMPORARILY_UNAVAILABLE",
        "RATE_LIMITED",
        "SIGNING_KEY_UNAVAILABLE",
        "INTERNAL_DECISION_FAILURE",
        "ACTIVATION_REJECTED",
    }
)

# Client-local codes -- never sent by Owner, produced entirely on-device by
# this package's own transport/verification/policy layers. Kept in a
# separate set (not unioned into PUBLIC_REASON_CODES) so a caller can always
# tell "Owner told us this" from "we decided this locally without ever
# reaching Owner."
LOCAL_REASON_CODES = frozenset(
    {
        "NETWORK_UNAVAILABLE",
        "REQUEST_TIMED_OUT",
        "TLS_VERIFICATION_FAILED",
        "MALFORMED_RESPONSE",
        "UNSIGNED_RESPONSE_REJECTED",
        "UNKNOWN_SIGNING_KEY",
        "ASSERTION_VERIFICATION_FAILED",
        "ASSERTION_EXPIRED",
        "ASSERTION_NOT_YET_VALID",
        "ASSERTION_PRODUCT_MISMATCH",
        "ASSERTION_PLATFORM_MISMATCH",
        "ASSERTION_INSTALLATION_MISMATCH",
        "ASSERTION_DEVICE_MISMATCH",
        "ASSERTION_FORBIDDEN_FIELD",
        "CLOCK_ROLLBACK_SUSPECTED",
        "LOCAL_STATE_CORRUPT",
        "DEVICE_KEY_UNAVAILABLE",
        "CAPABILITY_DENIED",
    }
)

ALL_CLIENT_REASON_CODES = PUBLIC_REASON_CODES | LOCAL_REASON_CODES


def is_success(reason_code: str) -> bool:
    return reason_code in SUCCESS_CODES


def is_from_owner(reason_code: str) -> bool:
    return reason_code in PUBLIC_REASON_CODES
