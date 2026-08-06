package com.actionaura.retail.licensing.lease

/**
 * M11 -- closed, client-local failure vocabulary for the signed-lease
 * verification pipeline. Mirrors the real split
 * `reason_codes.py`::`LOCAL_REASON_CODES` already establishes (codes
 * produced entirely on-device, never claimed to have come from
 * Owner) -- extended here with the decoding/context/time/policy
 * failure modes M11 itself introduces. Never carries a secret value
 * (matching `SecureStorageFailureCode`'s own M10 discipline).
 */
enum class LeaseFailureCode {
    // Decoding (M11.4)
    MALFORMED_ENVELOPE,
    OVERSIZED_LEASE,
    OVERSIZED_PAYLOAD,
    OVERSIZED_SIGNATURE,
    OVERSIZED_KEY_IDENTIFIER,
    OVERSIZED_STRING_FIELD,
    OVERSIZED_COLLECTION,
    EXCESSIVE_NESTING,
    INVALID_UTF8,
    DUPLICATE_FIELD,
    UNKNOWN_REQUIRED_FIELD,
    FORBIDDEN_FIELD,
    MALFORMED_NUMBER,
    MALFORMED_TIMESTAMP,
    MALFORMED_BASE64,
    TRAILING_BYTES,
    UNSUPPORTED_ENVELOPE_VERSION,
    UNSUPPORTED_CLAIM_VERSION,

    // Cryptography (M11.2/M11.7/M11.8)
    UNSUPPORTED_ALGORITHM,
    UNKNOWN_SIGNING_KEY,
    RETIRED_SIGNING_KEY_INELIGIBLE,
    MALFORMED_PUBLIC_KEY,
    MALFORMED_SIGNATURE,
    SIGNATURE_INVALID,
    CRYPTO_PROVIDER_UNAVAILABLE,

    // Context binding (M11.10)
    PRODUCT_MISMATCH,
    PLATFORM_MISMATCH,
    INSTALLATION_MISMATCH,

    // Time (M11.12-16)
    NOT_YET_VALID,
    EXPIRED,
    CLOCK_ROLLBACK_SUSPECTED,
    TRUSTED_TIME_UNAVAILABLE,
    MONOTONIC_CLOCK_ANOMALY,

    // Replay/rollback (M11.16)
    LEASE_REPLAYED,
    LEASE_SEQUENCE_REGRESSION,

    // Entitlement/version (M11.11/M11.21)
    UNKNOWN_MANDATORY_ENTITLEMENT,
    MALFORMED_ENTITLEMENT,
    APP_VERSION_BELOW_MINIMUM,
    MALFORMED_VERSION_CLAIM,

    // Generic
    UNKNOWN_SAFE_FAILURE,
}

/** Never format a secret value into this message -- matches `SecureStorageFailure`'s own M10 rule. */
data class LeaseVerificationFailure(val code: LeaseFailureCode, val safeDiagnosticReason: String? = null) {
    override fun toString(): String = "LeaseVerificationFailure(code=$code${safeDiagnosticReason?.let { ", reason=$it" } ?: ""})"
}
