package com.actionaura.retail.licensing.transport

import com.actionaura.retail.licensing.LicensingError

/**
 * M9.2 -- real, closed transport-result wrapper
 * (`external-licensing-transport-contract.md`). Every
 * [ExternalLicensingTransport] operation returns this, never a raw
 * exception or a bare nullable -- presentation code must never see a
 * platform-specific exception type.
 */
sealed interface TransportOutcome<out T> {
    data class Success<T>(val value: T) : TransportOutcome<T>
    data class BusinessRejection(val error: LicensingError) : TransportOutcome<Nothing>
    data class AuthenticationRejection(val error: LicensingError) : TransportOutcome<Nothing>
    data class RateLimited(val retryAfterSeconds: Long?) : TransportOutcome<Nothing>
    data object Timeout : TransportOutcome<Nothing>
    data class NetworkFailure(val reason: String) : TransportOutcome<Nothing>
    data object TlsFailure : TransportOutcome<Nothing>
    data class MalformedResponse(val reason: String) : TransportOutcome<Nothing>
    data class UnsupportedContractVersion(val serverVersion: String?) : TransportOutcome<Nothing>
    data object Cancelled : TransportOutcome<Nothing>
    data object TransportNotConfigured : TransportOutcome<Nothing>
}

inline fun <T, R> TransportOutcome<T>.map(transform: (T) -> R): TransportOutcome<R> = when (this) {
    is TransportOutcome.Success -> TransportOutcome.Success(transform(value))
    is TransportOutcome.BusinessRejection -> this
    is TransportOutcome.AuthenticationRejection -> this
    is TransportOutcome.RateLimited -> this
    is TransportOutcome.Timeout -> this
    is TransportOutcome.NetworkFailure -> this
    is TransportOutcome.TlsFailure -> this
    is TransportOutcome.MalformedResponse -> this
    is TransportOutcome.UnsupportedContractVersion -> this
    is TransportOutcome.Cancelled -> this
    is TransportOutcome.TransportNotConfigured -> this
}

/** Real, closed classification of whether a [TransportOutcome] is safe to retry -- mirrors OwnerClient.kt's own real, tested policy (mobile-licensing-network-audit.md). */
fun <T> TransportOutcome<T>.isSafeToRetry(): Boolean = when (this) {
    is TransportOutcome.RateLimited, is TransportOutcome.Timeout,
    is TransportOutcome.NetworkFailure,
    -> true
    is TransportOutcome.Success, is TransportOutcome.BusinessRejection,
    is TransportOutcome.AuthenticationRejection, is TransportOutcome.TlsFailure,
    is TransportOutcome.MalformedResponse, is TransportOutcome.UnsupportedContractVersion,
    is TransportOutcome.Cancelled, is TransportOutcome.TransportNotConfigured,
    -> false
}
