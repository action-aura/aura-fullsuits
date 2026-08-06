package com.actionaura.retail.licensing.lease

/**
 * M11.12/M11.13/M11.14/M11.15 -- real, exact port of `trusted_time.py`
 * (`canonical-signed-lease-authority-audit.md`). Trusted time is
 * **never** a fresh read of local wall clock -- it is monotonic
 * elapsed time added to the last Owner-verified server-time anchor.
 * A wall-clock forward jump cannot corrupt this computation by
 * construction, since [trustedNowMillis] never re-reads wall clock at
 * all; only [detectRollback] consults wall clock, and only to detect
 * backward movement -- the same real architectural property the
 * Python reference has.
 */
data class TrustedTimeAnchor(
    val serverTimeEpochMillis: Long,
    val monotonicAtAnchorMillis: Long,
)

sealed interface TrustedTimeConfidence {
    /** A real Owner-verified timestamp was observed and anchored within this process's own lifetime. */
    data object TrustedOnlineObservation : TrustedTimeConfidence
    /** No fresh online observation this process run, but a persisted anchor was re-pinned to a fresh monotonic reading at load time -- real, still trustworthy elapsed-time math, just not freshly confirmed against Owner. */
    data object TrustedMonotonicEstimate : TrustedTimeConfidence
    /** No anchor exists at all (never activated, or secure material unavailable) -- only the raw, untrusted local wall clock is available. */
    data object WallClockOnlyLimitedConfidence : TrustedTimeConfidence
    data object ClockRollbackSuspected : TrustedTimeConfidence
    data object MonotonicBaselineReset : TrustedTimeConfidence
    data object TrustUnavailable : TrustedTimeConfidence
    data object RefreshRequired : TrustedTimeConfidence
}

class TrustedTimeError(message: String) : IllegalStateException(message)

object TrustedTimeAuthority {

    fun newAnchor(serverTimeEpochMillis: Long, monotonicClock: MonotonicClock): TrustedTimeAnchor =
        TrustedTimeAnchor(serverTimeEpochMillis, monotonicClock.elapsedMillis())

    /** Real, exact port of `rehydrate_anchor` -- re-pins a persisted anchor's wall-clock value to a FRESH monotonic reading after a process restart, carrying `serverTimeEpochMillis` forward unchanged. Never call this lazily on every resolution (the real, already-discovered-and-fixed Python defect this file's own KDoc documents) -- call it exactly once per real process start, immediately after loading the persisted anchor. */
    fun rehydrateAnchor(persistedServerTimeEpochMillis: Long, monotonicClock: MonotonicClock): TrustedTimeAnchor =
        newAnchor(persistedServerTimeEpochMillis, monotonicClock)

    /** Real, exact port of `trusted_now()`. A negative monotonic delta fails closed (never silently treated as zero elapsed). */
    fun trustedNowMillis(anchor: TrustedTimeAnchor, monotonicClock: MonotonicClock): Long {
        val elapsed = monotonicClock.elapsedMillis() - anchor.monotonicAtAnchorMillis
        if (elapsed < 0) {
            throw TrustedTimeError("Monotonic clock moved backward -- refusing to compute trusted time.")
        }
        return anchor.serverTimeEpochMillis + elapsed
    }

    /** Real, exact port of `detect_rollback()` -- UTC-instant comparison only (both inputs are already epoch-millis, so timezone/DST representation differences never enter this comparison at all, unlike the Python reference which explicitly normalizes two `datetime` values to UTC first for the same reason). */
    fun detectRollback(anchor: TrustedTimeAnchor, localWallClockNowEpochMillis: Long, toleranceMillis: Long): Boolean {
        val deltaMillis = anchor.serverTimeEpochMillis - localWallClockNowEpochMillis
        return deltaMillis > toleranceMillis
    }
}
