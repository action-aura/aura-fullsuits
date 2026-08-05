package com.actionaura.retail.licensing.transport

import com.actionaura.retail.licensing.LicensingError
import com.actionaura.retail.licensing.LocalReasonCode
import com.actionaura.retail.licensing.ServerReasonCode

/**
 * M9.17 -- presentation-safe error category, reusing the stable M7.15
 * licensing error contract verbatim (`licensing-error-presentation-
 * map.md`). Never displays a raw exception; never collapses every
 * error into one generic message -- five real, distinct categories.
 */
sealed interface PresentationError {
    sealed interface CustomerAuthentication : PresentationError {
        data object InvalidCredentials : CustomerAuthentication
        data object AccountNotVerified : CustomerAuthentication
        data object AccountLocked : CustomerAuthentication
        data object AccountDisabled : CustomerAuthentication
        data object SessionExpired : CustomerAuthentication
        data object ReauthenticationRequired : CustomerAuthentication
    }

    sealed interface License : PresentationError {
        data object NotFoundOrNotOwned : License
        data object NotIssued : License
        data object Suspended : License
        data object Expired : License
        data object Revoked : License
        data object SubscriptionInactive : License
        data object ProductNotAllowed : License
        data object PlatformNotAllowed : License
    }

    sealed interface Installation : PresentationError {
        data object DeviceLimitReached : Installation
        data object Revoked : Installation
        data object Conflict : Installation
        data object IdentityConflict : Installation
        data object ReplacementRequired : Installation
    }

    sealed interface Transport : PresentationError {
        data object Offline : Transport
        data object Dns : Transport
        data object Timeout : Transport
        data object Tls : Transport
        data object ServerUnavailable : Transport
        data class RateLimited(val retryAfterSeconds: Long?) : Transport
        data object Cancelled : Transport
        data object NotConfigured : Transport
    }

    sealed interface Contract : PresentationError {
        data class MalformedResponse(val reason: String) : Contract
        data class UnsupportedVersion(val serverVersion: String?) : Contract
        data object UnknownRequiredField : Contract
        data object InconsistentDevicePolicy : Contract
        data object WrongProduct : Contract
        data object WrongPlatform : Contract
        data object WrongInstallation : Contract
    }

    data class Unknown(val raw: String) : PresentationError
}

/** Maps a real [TransportOutcome] failure into the closed [PresentationError] taxonomy above -- never displays the raw outcome directly. */
fun <T> TransportOutcome<T>.toPresentationError(): PresentationError? = when (this) {
    is TransportOutcome.Success -> null
    is TransportOutcome.BusinessRejection -> error.toPresentationError()
    is TransportOutcome.AuthenticationRejection -> error.toPresentationError()
    is TransportOutcome.RateLimited -> PresentationError.Transport.RateLimited(retryAfterSeconds)
    is TransportOutcome.Timeout -> PresentationError.Transport.Timeout
    is TransportOutcome.NetworkFailure -> PresentationError.Transport.Offline
    is TransportOutcome.TlsFailure -> PresentationError.Transport.Tls
    is TransportOutcome.MalformedResponse -> PresentationError.Contract.MalformedResponse(reason)
    is TransportOutcome.UnsupportedContractVersion -> PresentationError.Contract.UnsupportedVersion(serverVersion)
    is TransportOutcome.Cancelled -> PresentationError.Transport.Cancelled
    is TransportOutcome.TransportNotConfigured -> PresentationError.Transport.NotConfigured
}

fun LicensingError.toPresentationError(): PresentationError = when (this) {
    is LicensingError.FromServer -> code.toPresentationError()
    is LicensingError.FromServerUnknown -> PresentationError.Unknown(rawCode)
    is LicensingError.Local -> code.toPresentationError()
}

private fun ServerReasonCode.toPresentationError(): PresentationError = when (this) {
    ServerReasonCode.DEVICE_LIMIT_REACHED -> PresentationError.Installation.DeviceLimitReached
    ServerReasonCode.DEVICE_KEY_REVOKED -> PresentationError.Installation.Revoked
    ServerReasonCode.INSTALLATION_DEACTIVATED, ServerReasonCode.INSTALLATION_REPLACED -> PresentationError.Installation.ReplacementRequired
    ServerReasonCode.DEVICE_KEY_MISMATCH -> PresentationError.Installation.IdentityConflict
    ServerReasonCode.DEVICE_ALREADY_REGISTERED -> PresentationError.Installation.Conflict
    ServerReasonCode.PRODUCT_MISMATCH -> PresentationError.License.ProductNotAllowed
    ServerReasonCode.PLATFORM_NOT_ALLOWED -> PresentationError.License.PlatformNotAllowed
    ServerReasonCode.RELEASE_CHANNEL_NOT_ALLOWED -> PresentationError.License.PlatformNotAllowed
    ServerReasonCode.ACTIVATION_REJECTED -> PresentationError.License.NotFoundOrNotOwned
    ServerReasonCode.VERSION_NOT_ALLOWED, ServerReasonCode.VERSION_UNSUPPORTED -> PresentationError.Contract.UnsupportedVersion(null)
    ServerReasonCode.UNSUPPORTED_CONTRACT_VERSION -> PresentationError.Contract.UnsupportedVersion(null)
    ServerReasonCode.RATE_LIMITED -> PresentationError.Transport.RateLimited(null)
    ServerReasonCode.SERVICE_TEMPORARILY_UNAVAILABLE, ServerReasonCode.SIGNING_KEY_UNAVAILABLE -> PresentationError.Transport.ServerUnavailable
    ServerReasonCode.INVALID_REQUEST, ServerReasonCode.PAYLOAD_TOO_LARGE -> PresentationError.Contract.UnknownRequiredField
    ServerReasonCode.INVALID_SIGNATURE, ServerReasonCode.INVALID_PUBLIC_KEY -> PresentationError.Installation.IdentityConflict
    ServerReasonCode.IDEMPOTENCY_CONFLICT -> PresentationError.Contract.InconsistentDevicePolicy
    ServerReasonCode.INSTALLATION_NOT_FOUND -> PresentationError.Installation.Conflict
    ServerReasonCode.INSTALLATION_SUSPENDED -> PresentationError.License.Suspended
    ServerReasonCode.INTERNAL_DECISION_FAILURE -> PresentationError.Transport.ServerUnavailable
    ServerReasonCode.INVALID_TIMESTAMP, ServerReasonCode.TIMESTAMP_OUTSIDE_ALLOWED_WINDOW, ServerReasonCode.NONCE_REUSED -> PresentationError.Contract.MalformedResponse(name)
    ServerReasonCode.ACTIVATION_APPROVED, ServerReasonCode.ACTIVATION_ALREADY_ACTIVE,
    ServerReasonCode.CHECK_IN_ACCEPTED, ServerReasonCode.DEACTIVATION_ACCEPTED,
    -> PresentationError.Unknown(name) // success codes never reach an error mapper -- surfaced defensively, never silently swallowed
}

private fun LocalReasonCode.toPresentationError(): PresentationError = when (this) {
    LocalReasonCode.NETWORK_UNAVAILABLE -> PresentationError.Transport.Offline
    LocalReasonCode.REQUEST_TIMED_OUT -> PresentationError.Transport.Timeout
    LocalReasonCode.TLS_VERIFICATION_FAILED -> PresentationError.Transport.Tls
    LocalReasonCode.MALFORMED_RESPONSE -> PresentationError.Contract.MalformedResponse(name)
    LocalReasonCode.UNSIGNED_RESPONSE_REJECTED, LocalReasonCode.UNKNOWN_SIGNING_KEY,
    LocalReasonCode.ASSERTION_VERIFICATION_FAILED,
    -> PresentationError.Contract.MalformedResponse(name)
    LocalReasonCode.ASSERTION_EXPIRED, LocalReasonCode.ASSERTION_NOT_YET_VALID -> PresentationError.License.Expired
    LocalReasonCode.ASSERTION_PRODUCT_MISMATCH -> PresentationError.Contract.WrongProduct
    LocalReasonCode.ASSERTION_PLATFORM_MISMATCH -> PresentationError.Contract.WrongPlatform
    LocalReasonCode.ASSERTION_INSTALLATION_MISMATCH -> PresentationError.Contract.WrongInstallation
    LocalReasonCode.ASSERTION_DEVICE_MISMATCH -> PresentationError.Installation.IdentityConflict
    LocalReasonCode.ASSERTION_FORBIDDEN_FIELD -> PresentationError.Contract.MalformedResponse(name)
    LocalReasonCode.CLOCK_ROLLBACK_SUSPECTED -> PresentationError.Contract.MalformedResponse(name)
    LocalReasonCode.LOCAL_STATE_CORRUPT -> PresentationError.Contract.MalformedResponse(name)
    LocalReasonCode.DEVICE_KEY_UNAVAILABLE -> PresentationError.Installation.IdentityConflict
    LocalReasonCode.CAPABILITY_DENIED -> PresentationError.CustomerAuthentication.ReauthenticationRequired
}
