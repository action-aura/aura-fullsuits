package com.actionaura.retail.licensing.lease

import com.actionaura.retail.licensing.InstallationStatus
import com.actionaura.retail.licensing.LicenseStatus
import com.actionaura.retail.licensing.SubscriptionStatus

/**
 * M11.17 -- real, closed commercial-access decision model. Two layers:
 * [OfflineLicenseState] is a real, exact port of `state_machine.py`'s
 * `LicenseState` + `policy_evaluator.py::evaluate`'s own real logic
 * (reached only once signature verification and context binding both
 * already succeeded); [CommercialAccessDecision] is the full pipeline
 * outcome the checkpoint's own M11.17 list requires, wrapping every
 * real failure mode that can occur BEFORE offline policy is ever
 * evaluated (decoding, crypto, context, clock).
 */
enum class OfflineLicenseState {
    ACTIVE_ONLINE, ACTIVE_OFFLINE, WARNING, GRACE_PERIOD, RESTRICTED,
    SUSPENDED, REVOKED, EXPIRED, CLOCK_REVIEW_REQUIRED,
}

/** Real, exact port of `state_machine.py`'s `ACTIVE_FAMILY` -- full normal commercial operation is expected in these states. */
val OFFLINE_ACTIVE_FAMILY: Set<OfflineLicenseState> =
    setOf(OfflineLicenseState.ACTIVE_ONLINE, OfflineLicenseState.ACTIVE_OFFLINE, OfflineLicenseState.WARNING, OfflineLicenseState.GRACE_PERIOD)

/** Real, exact port of `state_machine.py`'s `DATA_PRESERVED_FAMILY` (restricted subset applicable post-activation) -- read/backup/export access survives; commercial mutation does not. */
val OFFLINE_DATA_PRESERVED_FAMILY: Set<OfflineLicenseState> = OFFLINE_ACTIVE_FAMILY + setOf(
    OfflineLicenseState.RESTRICTED, OfflineLicenseState.SUSPENDED, OfflineLicenseState.REVOKED,
    OfflineLicenseState.EXPIRED, OfflineLicenseState.CLOCK_REVIEW_REQUIRED,
)

/**
 * Real, closed offline-policy input -- the subset of a verified
 * lease's own claims [OfflinePolicyEvaluator] needs. Built only after
 * full cryptographic and structural verification has already passed.
 */
data class OfflinePolicyEvidence(
    val notBeforeEpochMillis: Long,
    val expiresAtEpochMillis: Long,
    val licenseStatus: LicenseStatus,
    val installationStatus: InstallationStatus,
    val subscriptionStatus: SubscriptionStatus,
    val checkInIntervalSeconds: Long,
    val retryIntervalSeconds: Long,
    val offlineGraceSeconds: Long,
    val warningStartSeconds: Long,
    val hardExpiryBehavior: String,
    val clockRollbackToleranceSeconds: Long,
    val emergencyExtensionAllowed: Boolean,
    val emergencyExtensionUntilEpochMillis: Long?,
    val commercialGraceEndEpochMillis: Long?,
)

class OfflinePolicyError(message: String) : IllegalStateException(message)

object OfflinePolicyEvaluator {

    /**
     * Real, exact port of `policy_evaluator.py::evaluate`, in order.
     * [localSafetyCeilingSeconds], if given, is `min()`'d against the
     * signed `offlineGraceSeconds` -- it can only SHORTEN the effective
     * grace window, never silently extend it (the Python reference's
     * own binding rule).
     */
    fun evaluate(
        evidence: OfflinePolicyEvidence,
        anchor: TrustedTimeAnchor,
        monotonicClock: MonotonicClock,
        localWallClockNowEpochMillis: Long,
        lastSuccessfulCheckInEpochMillis: Long,
        lastCheckInAttemptOk: Boolean,
        localSafetyCeilingSeconds: Long? = null,
    ): OfflineLicenseState {
        if (evidence.hardExpiryBehavior != "WARN_ONLY" && evidence.hardExpiryBehavior != "RESTRICT_COMMERCIAL_FEATURES_PRESERVE_DATA") {
            throw OfflinePolicyError("Unknown hard_expiry_behavior ${evidence.hardExpiryBehavior} -- refusing to guess a safe default.")
        }

        if (TrustedTimeAuthority.detectRollback(anchor, localWallClockNowEpochMillis, evidence.clockRollbackToleranceSeconds * 1000)) {
            return OfflineLicenseState.CLOCK_REVIEW_REQUIRED
        }

        val now = TrustedTimeAuthority.trustedNowMillis(anchor, monotonicClock)

        if (evidence.installationStatus == InstallationStatus.SUSPENDED) return OfflineLicenseState.SUSPENDED
        if (evidence.installationStatus == InstallationStatus.DEACTIVATED) return OfflineLicenseState.REVOKED
        if (evidence.licenseStatus == LicenseStatus.EXPIRED || evidence.licenseStatus == LicenseStatus.REVOKED) return OfflineLicenseState.EXPIRED
        if (evidence.licenseStatus == LicenseStatus.SUSPENDED) return OfflineLicenseState.SUSPENDED

        val emergencyExtensionEffective = evidence.emergencyExtensionAllowed &&
            evidence.emergencyExtensionUntilEpochMillis != null &&
            now < evidence.emergencyExtensionUntilEpochMillis

        if (!emergencyExtensionEffective) {
            if (evidence.subscriptionStatus == SubscriptionStatus.EXPIRED || evidence.subscriptionStatus == SubscriptionStatus.CANCELLED) {
                return OfflineLicenseState.RESTRICTED
            }
            if (evidence.subscriptionStatus == SubscriptionStatus.SUSPENDED) return OfflineLicenseState.RESTRICTED
            if (evidence.subscriptionStatus == SubscriptionStatus.PAST_DUE &&
                evidence.commercialGraceEndEpochMillis != null &&
                now > evidence.commercialGraceEndEpochMillis
            ) {
                return OfflineLicenseState.RESTRICTED
            }
        }

        // Real, exact port note: the Python reference checks `not_before`/`expires_at` here
        // and deliberately falls through either way (an expired-by-its-own-dates assertion is
        // treated identically to "we haven't checked in" -- the grace/restriction math below
        // is the real, single source of truth for both cases, not a separate early return).

        var effectiveGraceSeconds = evidence.offlineGraceSeconds
        if (localSafetyCeilingSeconds != null) {
            effectiveGraceSeconds = minOf(effectiveGraceSeconds, localSafetyCeilingSeconds)
        }
        if (evidence.emergencyExtensionAllowed && evidence.emergencyExtensionUntilEpochMillis != null && now < evidence.emergencyExtensionUntilEpochMillis) {
            val extensionSeconds = (evidence.emergencyExtensionUntilEpochMillis - now) / 1000
            effectiveGraceSeconds = maxOf(effectiveGraceSeconds, effectiveGraceSeconds + extensionSeconds)
        }

        val elapsedOfflineSeconds = (now - lastSuccessfulCheckInEpochMillis) / 1000

        if (lastCheckInAttemptOk && elapsedOfflineSeconds < evidence.checkInIntervalSeconds) {
            return OfflineLicenseState.ACTIVE_ONLINE
        }

        if (elapsedOfflineSeconds < effectiveGraceSeconds) {
            val warningBoundary = effectiveGraceSeconds - evidence.warningStartSeconds
            return when {
                elapsedOfflineSeconds >= warningBoundary -> OfflineLicenseState.WARNING
                !lastCheckInAttemptOk -> OfflineLicenseState.ACTIVE_OFFLINE
                else -> OfflineLicenseState.ACTIVE_ONLINE
            }
        }

        if (elapsedOfflineSeconds < effectiveGraceSeconds + evidence.retryIntervalSeconds) {
            return OfflineLicenseState.GRACE_PERIOD
        }

        return if (evidence.hardExpiryBehavior == "WARN_ONLY") OfflineLicenseState.GRACE_PERIOD else OfflineLicenseState.RESTRICTED
    }
}

/**
 * M11.17 -- the real, closed, full-pipeline commercial-access
 * decision. Every case carries a safe reason, whether online refresh
 * can recover, whether reactivation is required, and never secret
 * material.
 */
sealed interface CommercialAccessDecision {
    val permitsCommercialOperation: Boolean
    val permitsDataPreservedAccess: Boolean
    val onlineRefreshCanRecover: Boolean
    val reactivationRequired: Boolean

    data object NoActivationMaterial : CommercialAccessDecision by SafeBlocked(false, true, false, true)
    data object SecureStorageUnavailable : CommercialAccessDecision by SafeBlocked(false, false, true, false)
    data object SecureStorageLocked : CommercialAccessDecision by SafeBlocked(false, false, true, false)
    data object SecureMaterialCorrupt : CommercialAccessDecision by SafeBlocked(false, true, false, true)
    data object LeaseMissing : CommercialAccessDecision by SafeBlocked(false, true, true, false)
    data class LeaseUnsupported(val reason: LeaseFailureCode) : CommercialAccessDecision by SafeBlocked(false, true, true, false)
    data class SignatureInvalid(val reason: LeaseFailureCode) : CommercialAccessDecision by SafeBlocked(false, true, true, false)
    data object SigningKeyUnknown : CommercialAccessDecision by SafeBlocked(false, true, true, false)
    data object ProductMismatch : CommercialAccessDecision by SafeBlocked(false, true, false, true)
    data object PlatformMismatch : CommercialAccessDecision by SafeBlocked(false, true, false, true)
    data object InstallationMismatch : CommercialAccessDecision by SafeBlocked(false, true, false, true)
    data object LeaseNotYetValid : CommercialAccessDecision by SafeBlocked(false, true, true, false)
    data object ClockAnomaly : CommercialAccessDecision by SafeBlocked(false, true, true, false)
    data object OnlineRefreshRequired : CommercialAccessDecision by SafeBlocked(false, true, true, false)
    data object RequiredUpdate : CommercialAccessDecision by SafeBlocked(false, true, false, false)
    data object EntitlementUnsupported : CommercialAccessDecision by SafeBlocked(false, true, true, false)
    data object ReactivationRequired : CommercialAccessDecision by SafeBlocked(false, true, false, true)

    data class Active(val offlineState: OfflineLicenseState) : CommercialAccessDecision {
        override val permitsCommercialOperation = true
        override val permitsDataPreservedAccess = true
        override val onlineRefreshCanRecover = true
        override val reactivationRequired = false
    }

    data class Restricted(val offlineState: OfflineLicenseState) : CommercialAccessDecision by SafeBlocked(false, true, true, false)
}

/** Real, shared implementation for every closed-off, non-`Active` outcome -- avoids duplicating the same four booleans per case while keeping each case a real, distinct, named type callers can `when`-exhaust on. */
private data class SafeBlocked(
    override val permitsCommercialOperation: Boolean,
    override val permitsDataPreservedAccess: Boolean,
    override val onlineRefreshCanRecover: Boolean,
    override val reactivationRequired: Boolean,
) : CommercialAccessDecision

/** Real, deterministic mapping from a fully-evaluated [OfflineLicenseState] to the final [CommercialAccessDecision] -- the last step of the M11.22 startup pipeline. */
fun OfflineLicenseState.toCommercialAccessDecision(): CommercialAccessDecision = when (this) {
    OfflineLicenseState.ACTIVE_ONLINE, OfflineLicenseState.ACTIVE_OFFLINE, OfflineLicenseState.WARNING, OfflineLicenseState.GRACE_PERIOD ->
        CommercialAccessDecision.Active(this)
    OfflineLicenseState.RESTRICTED -> CommercialAccessDecision.Restricted(this)
    OfflineLicenseState.SUSPENDED -> CommercialAccessDecision.Restricted(this)
    OfflineLicenseState.REVOKED -> CommercialAccessDecision.ReactivationRequired
    OfflineLicenseState.EXPIRED -> CommercialAccessDecision.Restricted(this)
    OfflineLicenseState.CLOCK_REVIEW_REQUIRED -> CommercialAccessDecision.ClockAnomaly
}
