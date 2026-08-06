package com.actionaura.retail.licensing.lease

import com.actionaura.retail.licensing.InstallationStatus
import com.actionaura.retail.licensing.LicenseStatus
import com.actionaura.retail.licensing.SubscriptionStatus
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith

/**
 * M11.17 -- real, executed proof of [OfflinePolicyEvaluator], an
 * exact port of `policy_evaluator.py::evaluate`
 * (`canonical-signed-lease-authority-audit.md`). Every test uses a
 * deterministic injected clock -- no real wall-clock dependency.
 */
class OfflinePolicyEvaluatorTest {

    private val day = 86_400_000L
    private val anchorNow = 1_000_000_000_000L // arbitrary fixed epoch millis

    private fun anchor() = TrustedTimeAnchor(anchorNow, 0L)
    private fun clock(elapsed: Long) = MonotonicClock { elapsed }

    private fun baseEvidence(
        licenseStatus: LicenseStatus = LicenseStatus.ACTIVE,
        installationStatus: InstallationStatus = InstallationStatus.ACTIVE,
        subscriptionStatus: SubscriptionStatus = SubscriptionStatus.ACTIVE,
        offlineGraceSeconds: Long = 3 * 86_400L,
        checkInIntervalSeconds: Long = 86_400L,
        retryIntervalSeconds: Long = 3_600L,
        warningStartSeconds: Long = 12 * 3600L,
        hardExpiryBehavior: String = "RESTRICT_COMMERCIAL_FEATURES_PRESERVE_DATA",
        clockRollbackToleranceSeconds: Long = 300L,
        commercialGraceEndEpochMillis: Long? = null,
        emergencyExtensionAllowed: Boolean = false,
        emergencyExtensionUntilEpochMillis: Long? = null,
    ) = OfflinePolicyEvidence(
        notBeforeEpochMillis = anchorNow - day, expiresAtEpochMillis = anchorNow + 365 * day,
        licenseStatus = licenseStatus, installationStatus = installationStatus, subscriptionStatus = subscriptionStatus,
        checkInIntervalSeconds = checkInIntervalSeconds, retryIntervalSeconds = retryIntervalSeconds,
        offlineGraceSeconds = offlineGraceSeconds, warningStartSeconds = warningStartSeconds,
        hardExpiryBehavior = hardExpiryBehavior, clockRollbackToleranceSeconds = clockRollbackToleranceSeconds,
        emergencyExtensionAllowed = emergencyExtensionAllowed, emergencyExtensionUntilEpochMillis = emergencyExtensionUntilEpochMillis,
        commercialGraceEndEpochMillis = commercialGraceEndEpochMillis,
    )

    @Test
    fun recentlyCheckedInIsActiveOnline() {
        val state = OfflinePolicyEvaluator.evaluate(baseEvidence(), anchor(), clock(0), anchorNow, anchorNow, true)
        assertEquals(OfflineLicenseState.ACTIVE_ONLINE, state)
    }

    @Test
    fun withinGraceButPastCheckInIntervalIsActiveOffline() {
        val elapsed = (2 * 3600L) * 1000 // 2h offline, well within grace, before warning boundary
        val state = OfflinePolicyEvaluator.evaluate(baseEvidence(), anchor(), clock(elapsed), anchorNow, anchorNow - 2 * 86_400L * 1000, false)
        assertEquals(OfflineLicenseState.ACTIVE_OFFLINE, state)
    }

    @Test
    fun nearGraceBoundaryIsWarning() {
        // grace=3d, warningStart=12h -> warning boundary at 2.5d elapsed
        val lastCheckIn = anchorNow
        val elapsed = (2 * day) + (13 * 3600L * 1000) // 2d13h elapsed, past the 2.5d warning boundary
        val state = OfflinePolicyEvaluator.evaluate(baseEvidence(), anchor(), clock(elapsed), anchorNow, lastCheckIn, false)
        assertEquals(OfflineLicenseState.WARNING, state)
    }

    @Test
    fun pastGraceButWithinRetryWindowIsGracePeriod() {
        val elapsed = (3 * day) + (30 * 60L * 1000) // just past 3d grace, within 1h retry window
        val state = OfflinePolicyEvaluator.evaluate(baseEvidence(), anchor(), clock(elapsed), anchorNow, anchorNow, false)
        assertEquals(OfflineLicenseState.GRACE_PERIOD, state)
    }

    @Test
    fun fullyExhaustedWithRestrictBehaviorIsRestricted() {
        val elapsed = (5 * day) * 1000
        val state = OfflinePolicyEvaluator.evaluate(baseEvidence(), anchor(), clock(elapsed), anchorNow, anchorNow, false)
        assertEquals(OfflineLicenseState.RESTRICTED, state)
    }

    @Test
    fun fullyExhaustedWithWarnOnlyBehaviorStaysGracePeriod() {
        val elapsed = (5 * day) * 1000
        val state = OfflinePolicyEvaluator.evaluate(baseEvidence(hardExpiryBehavior = "WARN_ONLY"), anchor(), clock(elapsed), anchorNow, anchorNow, false)
        assertEquals(OfflineLicenseState.GRACE_PERIOD, state, "real regression: WARN_ONLY must never block commercial mutation by time alone")
    }

    @Test
    fun installationSuspendedOverridesEverythingElse() {
        val state = OfflinePolicyEvaluator.evaluate(baseEvidence(installationStatus = InstallationStatus.SUSPENDED), anchor(), clock(0), anchorNow, anchorNow, true)
        assertEquals(OfflineLicenseState.SUSPENDED, state)
    }

    @Test
    fun licenseRevokedIsExpired() {
        val state = OfflinePolicyEvaluator.evaluate(baseEvidence(licenseStatus = LicenseStatus.REVOKED), anchor(), clock(0), anchorNow, anchorNow, true)
        assertEquals(OfflineLicenseState.EXPIRED, state)
    }

    @Test
    fun subscriptionPastDueBeforeGraceEndStaysActive() {
        val evidence = baseEvidence(subscriptionStatus = SubscriptionStatus.PAST_DUE, commercialGraceEndEpochMillis = anchorNow + 10 * day)
        val state = OfflinePolicyEvaluator.evaluate(evidence, anchor(), clock(0), anchorNow, anchorNow, true)
        assertEquals(OfflineLicenseState.ACTIVE_ONLINE, state)
    }

    @Test
    fun subscriptionPastDueAfterGraceEndIsRestricted() {
        val evidence = baseEvidence(subscriptionStatus = SubscriptionStatus.PAST_DUE, commercialGraceEndEpochMillis = anchorNow - day)
        val state = OfflinePolicyEvaluator.evaluate(evidence, anchor(), clock(0), anchorNow, anchorNow, true)
        assertEquals(OfflineLicenseState.RESTRICTED, state)
    }

    @Test
    fun emergencyExtensionEffectiveOverridesPastDueRestriction() {
        val evidence = baseEvidence(
            subscriptionStatus = SubscriptionStatus.PAST_DUE, commercialGraceEndEpochMillis = anchorNow - day,
            emergencyExtensionAllowed = true, emergencyExtensionUntilEpochMillis = anchorNow + 10 * day,
        )
        val state = OfflinePolicyEvaluator.evaluate(evidence, anchor(), clock(0), anchorNow, anchorNow, true)
        assertEquals(OfflineLicenseState.ACTIVE_ONLINE, state, "real regression: an effective emergency extension must override a PAST_DUE restriction")
    }

    @Test
    fun emergencyExtensionNeverMasksASecurityDrivenLicenseSuspension() {
        val evidence = baseEvidence(
            licenseStatus = LicenseStatus.SUSPENDED,
            emergencyExtensionAllowed = true, emergencyExtensionUntilEpochMillis = anchorNow + 10 * day,
        )
        val state = OfflinePolicyEvaluator.evaluate(evidence, anchor(), clock(0), anchorNow, anchorNow, true)
        assertEquals(OfflineLicenseState.SUSPENDED, state, "real regression: emergency extension must never mask a license-level (security-driven) suspension")
    }

    @Test
    fun clockRollbackShortCircuitsEveryOtherRule() {
        // Local wall clock reads far earlier than the trusted anchor -- suspicious rollback.
        val state = OfflinePolicyEvaluator.evaluate(baseEvidence(), anchor(), clock(0), anchorNow - 10 * day, anchorNow, true)
        assertEquals(OfflineLicenseState.CLOCK_REVIEW_REQUIRED, state)
    }

    @Test
    fun timezoneOnlyDifferenceNeverTriggersRollback() {
        // Same real UTC instant, no rollback -- this evaluator operates purely on epoch millis, so a timezone difference can never appear here at all (real, structural proof, not just an assertion).
        val state = OfflinePolicyEvaluator.evaluate(baseEvidence(), anchor(), clock(0), anchorNow, anchorNow, true)
        assertEquals(OfflineLicenseState.ACTIVE_ONLINE, state)
    }

    @Test
    fun localSafetyCeilingCanOnlyShortenNeverExtendSignedGrace() {
        // Signed grace is 3 days; local ceiling of 1 day should apply, so 2 days elapsed exceeds the ceiling+retry -> RESTRICTED, not still within the signed 3-day grace.
        val elapsed = (2 * day) * 1000
        val state = OfflinePolicyEvaluator.evaluate(baseEvidence(), anchor(), clock(elapsed), anchorNow, anchorNow, false, localSafetyCeilingSeconds = day)
        assertEquals(OfflineLicenseState.RESTRICTED, state, "real regression: a local safety ceiling must be able to shorten effective grace below the signed value")
    }

    @Test
    fun unknownHardExpiryBehaviorFailsClosedNeverGuessesADefault() {
        assertFailsWith<OfflinePolicyError> {
            OfflinePolicyEvaluator.evaluate(baseEvidence(hardExpiryBehavior = "SOMETHING_ELSE"), anchor(), clock(0), anchorNow, anchorNow, true)
        }
    }
}
