"""Stable, machine-readable reason-code vocabulary (Part G).

PUBLIC codes are safe to return in an external HTTP response. INTERNAL_ONLY
codes may be recorded in owner_activation_requests / the audit log but are
normalized to a public code (usually ACTIVATION_REJECTED /
LICENSE_OR_DEVICE_INVALID) before ever reaching an external response, so a
caller cannot distinguish "wrong key" from "right key, wrong device" from
timing/response shape alone (anti-enumeration, Part G's explicit instruction).
"""
from __future__ import annotations

SUCCESS_CODES = frozenset(
    {"ACTIVATION_APPROVED", "ACTIVATION_ALREADY_ACTIVE", "CHECK_IN_ACCEPTED", "DEACTIVATION_ACCEPTED"}
)

# Public: safe to return to any caller, including an unauthenticated one.
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
        "DEVICE_ALREADY_REGISTERED",
        "SERVICE_TEMPORARILY_UNAVAILABLE",
        "RATE_LIMITED",
        "SIGNING_KEY_UNAVAILABLE",
        "INTERNAL_DECISION_FAILURE",
        # Deliberately normalized/generic public code for the whole license-
        # validity family below -- see _PUBLIC_NORMALIZATION.
        "ACTIVATION_REJECTED",
    }
)

# Internal-only: precise, useful for staff/audit, but never returned verbatim
# externally -- returning them would materially aid license-key enumeration
# (e.g. distinguishing LICENSE_NOT_FOUND from LICENSE_SUSPENDED lets an
# attacker learn a guessed key was real just suspended).
INTERNAL_ONLY_REASON_CODES = frozenset(
    {
        "LICENSE_NOT_FOUND",
        "LICENSE_NOT_ISSUED",
        "LICENSE_NOT_ACTIVE",
        "LICENSE_SUSPENDED",
        "LICENSE_EXPIRED",
        "LICENSE_REVOKED",
        "LICENSE_REPLACED",
        "LICENSE_NOT_YET_VALID",
        "SUBSCRIPTION_INACTIVE",
        "SUBSCRIPTION_SUSPENDED",
        "SUBSCRIPTION_EXPIRED",
        "SUBSCRIPTION_CANCELLED",
    }
)

ALL_REASON_CODES = PUBLIC_REASON_CODES | INTERNAL_ONLY_REASON_CODES

# The public code substituted for every internal-only code when building an
# external response. Chosen so a caller cannot tell "license doesn't exist"
# apart from "license exists but is suspended/expired/revoked/not-yet-valid" --
# all normalize to the same rejection at the activation boundary. Check-in
# (post-activation, already proven device) is allowed to be a bit more
# specific since the caller has already proven possession of a real,
# previously-activated device -- but still never differentiates
# not-found-style ambiguity.
_PUBLIC_NORMALIZATION = {code: "ACTIVATION_REJECTED" for code in INTERNAL_ONLY_REASON_CODES}


def to_public_reason_code(internal_code: str) -> str:
    """Maps any reason code (public or internal) to the code safe to place in
    an external HTTP response body."""
    if internal_code in PUBLIC_REASON_CODES:
        return internal_code
    return _PUBLIC_NORMALIZATION.get(internal_code, "ACTIVATION_REJECTED")


def is_success(reason_code: str) -> bool:
    return reason_code in SUCCESS_CODES
