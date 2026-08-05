package com.actionaura.retail.licensing

/**
 * M8.14 -- sanitized, versioned compatibility fixture matrix
 * (`device-policy-fixture-report.md`). No real license identifiers,
 * PII, or secrets. Fixtures marked FUTURE/TARGET are explicitly
 * documented as such -- they never imply current production Owner
 * already accepts iOS.
 */
object DevicePolicyFixtures {
    const val FIXTURE_SET_VERSION = "m8-device-policy-fixtures-v1"

    private fun policy(
        totalLimit: Int,
        activeCount: Int,
        remaining: Int = totalLimit - activeCount,
        allowedPlatforms: List<String> = listOf("WINDOWS", "ANDROID"),
        replacementAllowed: Boolean = true,
        voluntaryDeactivationAllowed: Boolean = true,
        policyVersion: Int = 1,
    ) = ResolvedDevicePolicy(
        policyVersion = policyVersion,
        licensePublicId = "fixture-license-public-id",
        productCode = LicensingProductCode.AURA_RETAIL,
        allowedPlatforms = allowedPlatforms,
        totalActiveInstallationLimit = totalLimit,
        currentActiveInstallationCount = activeCount,
        remainingInstallationSlots = remaining,
        voluntaryDeactivationAllowed = voluntaryDeactivationAllowed,
        replacementAllowed = replacementAllowed,
        policyEffectiveAt = "2026-08-05T00:00:00Z",
    )

    // -- Platform combinations --
    fun windowsOnlyPolicy() = policy(totalLimit = 2, activeCount = 1, allowedPlatforms = listOf("WINDOWS"))
    fun androidOnlyPolicy() = policy(totalLimit = 2, activeCount = 1, allowedPlatforms = listOf("ANDROID"))

    /** FUTURE/TARGET -- does not imply current production Owner accepts IOS today (ios-platform-readiness-state.md). */
    fun futureIosOnlyPolicy() = policy(totalLimit = 2, activeCount = 0, allowedPlatforms = listOf("IOS"))
    fun windowsAndAndroidPolicy() = policy(totalLimit = 3, activeCount = 2, allowedPlatforms = listOf("WINDOWS", "ANDROID"))

    /** FUTURE/TARGET. */
    fun androidAndIosPolicy() = policy(totalLimit = 3, activeCount = 1, allowedPlatforms = listOf("ANDROID", "IOS"))

    /** FUTURE/TARGET. */
    fun windowsAndroidIosPolicy() = policy(totalLimit = 5, activeCount = 2, allowedPlatforms = listOf("WINDOWS", "ANDROID", "IOS"))

    // -- Total-limit variants --
    fun totalLimitOne() = policy(totalLimit = 1, activeCount = 0)
    fun totalLimitTwo() = policy(totalLimit = 2, activeCount = 1)
    fun totalLimitThree() = policy(totalLimit = 3, activeCount = 1)

    // -- Slot-count variants --
    fun noRemainingSlot() = policy(totalLimit = 2, activeCount = 2, remaining = 0)
    fun finalRemainingSlot() = policy(totalLimit = 3, activeCount = 2, remaining = 1)

    // -- Malformed variants (never trusted, never used to compute a local cap) --
    fun malformedNegativeLimit() = policy(totalLimit = -1, activeCount = 0, remaining = -1)
    fun malformedActiveCountAboveLimit() = policy(totalLimit = 1, activeCount = 3, remaining = -2)
    fun malformedInconsistentRemainingCount() = policy(totalLimit = 3, activeCount = 1, remaining = 99)
    fun malformedUnknownPlatform() = policy(totalLimit = 2, activeCount = 0, allowedPlatforms = listOf("WINDOWS", "LINUX"))
    fun malformedFuturePolicyVersion() = policy(totalLimit = 2, activeCount = 0, policyVersion = 99)

    // -- Replacement --
    fun replacementAllowedPolicy() = policy(totalLimit = 2, activeCount = 1, replacementAllowed = true)
    fun replacementDeniedPolicy() = policy(totalLimit = 2, activeCount = 1, replacementAllowed = false)

    // -- Installation-status fixtures --
    fun deactivatedInstallation() = InstallationDescriptor(
        installationId = "fixture-installation-deactivated", devicePublicKey = "FAKE_TEST_KEY==",
        appVersion = "1.0.0-fixture", platform = LicensingPlatform.ANDROID, status = InstallationStatus.DEACTIVATED,
    )
    fun revokedInstallation() = InstallationDescriptor(
        installationId = "fixture-installation-revoked", devicePublicKey = "FAKE_TEST_KEY==",
        appVersion = "1.0.0-fixture", platform = LicensingPlatform.ANDROID, status = InstallationStatus.REPLACED,
    )

    // -- Same-Installation retry --
    fun sameInstallationRetryPolicy() = policy(totalLimit = 1, activeCount = 1).copy(sameInstallationRetryConsumesSlot = false)

    // -- iOS readiness / customer-auth --

    /** Real, current, matches ios-platform-readiness-state.md's own current() snapshot. */
    fun ownerIosUnseededReadiness() = IosPlatformReadinessState.current()

    /** Real presentation state for the customer-auth prerequisite gap (OWNER-CUSTOMER-AUTH-PREREQUISITE-SPEC.md). */
    fun customerAuthUnavailableState(): DevicePolicyPresentationState = DevicePolicyPresentationState.CustomerAuthPrerequisiteMissing
}
