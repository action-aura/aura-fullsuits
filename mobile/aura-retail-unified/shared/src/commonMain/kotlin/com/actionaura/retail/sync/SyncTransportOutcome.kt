package com.actionaura.retail.sync

/**
 * Task 9 (multi-device-sync-foundation) -- real, closed result wrapper for
 * [SyncTransport], mirroring [com.actionaura.retail.licensing.transport.TransportOutcome]'s
 * own discipline (never a raw exception or bare nullable escapes the
 * transport) but shaped for the sync relay's own, simpler wire contract
 * (`owner/app/sync/routes.py`): every rejection there is a flat
 * `{"reason_code": "..."}` body (never a typed [com.actionaura.retail.licensing.LicensingError]),
 * so [Rejected] carries the raw server string directly rather than
 * reusing licensing's error taxonomy, which does not apply here.
 */
sealed interface SyncTransportOutcome<out T> {
    data class Success<T>(val value: T) : SyncTransportOutcome<T>

    /**
     * A real, well-formed rejection from Owner's own `_error()` helper
     * (`owner/app/sync/routes.py`) -- e.g. `INVALID_SIGNATURE`,
     * `NONCE_REUSED`, `INSTALLATION_SUSPENDED`, `INVALID_BATCH`,
     * `INVALID_EVENT`, `INVALID_SINCE`, `INTERNAL_ERROR`. [httpStatus] is
     * carried alongside for callers that want to distinguish a genuine
     * business/auth rejection (400) from a server-side failure (500)
     * without this transport itself guessing at retry policy.
     */
    data class Rejected(val reasonCode: String, val httpStatus: Int) : SyncTransportOutcome<Nothing>
    data class MalformedResponse(val reason: String) : SyncTransportOutcome<Nothing>
    data object Timeout : SyncTransportOutcome<Nothing>
    data object TlsFailure : SyncTransportOutcome<Nothing>
    data class NetworkFailure(val reason: String) : SyncTransportOutcome<Nothing>
}
