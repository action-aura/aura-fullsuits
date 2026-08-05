package com.actionaura.retail.licensing

/**
 * M7.17 -- shared error model (`licensing-error-contract.md`). Wraps the
 * real, exact server/client reason-code vocabularies verbatim -- no
 * renaming, no invented fourth category. An unrecognized server string
 * surfaces as [ServerReasonCode.Unknown] rather than being silently
 * coerced to an existing case (forward-compatibility discipline).
 */
enum class RetryGuidance { SAFE_TO_RETRY_WITH_BACKOFF, DO_NOT_RETRY_WITHOUT_CORRECTION }

/**
 * Real `PUBLIC_REASON_CODES`, `owner/app/licensing_service/reason_codes.py`
 * (31 values: 4 success + 27 non-success). Verbatim string match only --
 * this is not a translation layer.
 */
enum class ServerReasonCode {
    ACTIVATION_APPROVED, ACTIVATION_ALREADY_ACTIVE, CHECK_IN_ACCEPTED, DEACTIVATION_ACCEPTED,
    INVALID_REQUEST, UNSUPPORTED_CONTRACT_VERSION, INVALID_TIMESTAMP, TIMESTAMP_OUTSIDE_ALLOWED_WINDOW,
    NONCE_REUSED, INVALID_SIGNATURE, INVALID_PUBLIC_KEY, PAYLOAD_TOO_LARGE, IDEMPOTENCY_CONFLICT,
    PRODUCT_MISMATCH, PLATFORM_NOT_ALLOWED, RELEASE_CHANNEL_NOT_ALLOWED, VERSION_NOT_ALLOWED,
    VERSION_UNSUPPORTED, DEVICE_LIMIT_REACHED, INSTALLATION_NOT_FOUND, INSTALLATION_SUSPENDED,
    INSTALLATION_DEACTIVATED, INSTALLATION_REPLACED, DEVICE_KEY_MISMATCH, DEVICE_KEY_REVOKED,
    DEVICE_ALREADY_REGISTERED, SERVICE_TEMPORARILY_UNAVAILABLE, RATE_LIMITED, SIGNING_KEY_UNAVAILABLE,
    INTERNAL_DECISION_FAILURE, ACTIVATION_REJECTED;

    companion object {
        private val SUCCESS = setOf(ACTIVATION_APPROVED, ACTIVATION_ALREADY_ACTIVE, CHECK_IN_ACCEPTED, DEACTIVATION_ACCEPTED)
        private val RETRYABLE = setOf(RATE_LIMITED, SERVICE_TEMPORARILY_UNAVAILABLE)

        /** Returns null (never throws) for a string outside the real vocabulary -- callers must handle [ServerReasonCode.Unknown]. */
        fun parseOrNull(raw: String): ServerReasonCode? = entries.firstOrNull { it.name == raw }
    }

    fun isSuccess(): Boolean = this in SUCCESS
    fun defaultRetryGuidance(): RetryGuidance = if (this in RETRYABLE) RetryGuidance.SAFE_TO_RETRY_WITH_BACKOFF else RetryGuidance.DO_NOT_RETRY_WITHOUT_CORRECTION
}

/**
 * Real `LOCAL_REASON_CODES`, `commercial_runtime/licensing_contracts/reason_codes.py`
 * (18 values). Produced entirely on-device -- Owner never sends these.
 */
enum class LocalReasonCode {
    NETWORK_UNAVAILABLE, REQUEST_TIMED_OUT, TLS_VERIFICATION_FAILED, MALFORMED_RESPONSE,
    UNSIGNED_RESPONSE_REJECTED, UNKNOWN_SIGNING_KEY, ASSERTION_VERIFICATION_FAILED, ASSERTION_EXPIRED,
    ASSERTION_NOT_YET_VALID, ASSERTION_PRODUCT_MISMATCH, ASSERTION_PLATFORM_MISMATCH,
    ASSERTION_INSTALLATION_MISMATCH, ASSERTION_DEVICE_MISMATCH, ASSERTION_FORBIDDEN_FIELD,
    CLOCK_ROLLBACK_SUSPECTED, LOCAL_STATE_CORRUPT, DEVICE_KEY_UNAVAILABLE, CAPABILITY_DENIED;

    companion object {
        private val RETRYABLE = setOf(NETWORK_UNAVAILABLE, REQUEST_TIMED_OUT)
    }

    fun defaultRetryGuidance(): RetryGuidance = if (this in RETRYABLE) RetryGuidance.SAFE_TO_RETRY_WITH_BACKOFF else RetryGuidance.DO_NOT_RETRY_WITHOUT_CORRECTION
}

/** Closed, exhaustive licensing error -- exactly the three real categories from `licensing-error-contract.md`. */
sealed interface LicensingError {
    val retryGuidance: RetryGuidance

    data class FromServer(val code: ServerReasonCode, override val retryGuidance: RetryGuidance = code.defaultRetryGuidance()) : LicensingError
    data class FromServerUnknown(val rawCode: String, override val retryGuidance: RetryGuidance = RetryGuidance.DO_NOT_RETRY_WITHOUT_CORRECTION) : LicensingError
    data class Local(val code: LocalReasonCode, override val retryGuidance: RetryGuidance = code.defaultRetryGuidance()) : LicensingError

    companion object {
        /** Parses a raw server reason-code string into a [LicensingError], never throwing on an unrecognized value. */
        fun fromServerCode(raw: String, retryGuidance: RetryGuidance? = null): LicensingError {
            val known = ServerReasonCode.parseOrNull(raw)
            return if (known != null) {
                FromServer(known, retryGuidance ?: known.defaultRetryGuidance())
            } else {
                FromServerUnknown(raw, retryGuidance ?: RetryGuidance.DO_NOT_RETRY_WITHOUT_CORRECTION)
            }
        }
    }
}
