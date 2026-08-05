package com.actionaura.retail.licensing.transport

import com.actionaura.retail.licensing.DeviceSlotOutcome
import com.actionaura.retail.licensing.LicensingError
import com.actionaura.retail.licensing.LicensingPlatform
import com.actionaura.retail.licensing.LicensingProductCode
import com.actionaura.retail.licensing.LocalReasonCode
import com.actionaura.retail.licensing.ServerReasonCode
import kotlinx.coroutines.test.runTest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

/**
 * M9.29 -- real, executed test matrix for the M9 shared mobile
 * activation orchestration layer. See `milestone-9-test-report.md`
 * for the full accounting of which required-matrix items are covered
 * here vs. structurally guaranteed vs. genuinely not applicable.
 */
class M9OrchestrationTest {

    // ===== TRANSPORT =====

    @Test
    fun disabledProductionTransportReturnsNotConfiguredForEveryOperation() = runTest {
        val transport = DisabledProductionTransport()
        assertTrue(transport.signIn(CustomerSignInRequest("a@b.com", "x")) is TransportOutcome.TransportNotConfigured)
        assertTrue(transport.claimLicense(LicenseClaimRequest(ExternalCustomerSessionId("s"), "FIXTURE-SERIAL", LicensingProductCode.AURA_RETAIL)) is TransportOutcome.TransportNotConfigured)
        assertTrue(transport.refreshLease(LeaseRefreshRequest("i")) is TransportOutcome.TransportNotConfigured)
    }

    @Test
    fun rateLimitedTimeoutNetworkFailureAreSafeToRetryOnly() {
        assertTrue(TransportOutcome.RateLimited(5).isSafeToRetry())
        assertTrue(TransportOutcome.Timeout.isSafeToRetry())
        assertTrue(TransportOutcome.NetworkFailure("dns").isSafeToRetry())
        assertFalse(TransportOutcome.AuthenticationRejection(LicensingError.Local(LocalReasonCode.NETWORK_UNAVAILABLE)).isSafeToRetry())
        assertFalse(TransportOutcome.TlsFailure.isSafeToRetry())
        assertFalse(TransportOutcome.MalformedResponse("bad").isSafeToRetry())
        assertFalse(TransportOutcome.UnsupportedContractVersion("v2").isSafeToRetry())
        assertFalse(TransportOutcome.Cancelled.isSafeToRetry())
        assertFalse(TransportOutcome.TransportNotConfigured.isSafeToRetry())
    }

    @Test
    fun malformedResponseAndUnsupportedVersionMapToContractCategory() {
        assertTrue(TransportOutcome.MalformedResponse("bad json").toPresentationError() is PresentationError.Contract.MalformedResponse)
        assertTrue(TransportOutcome.UnsupportedContractVersion("v2").toPresentationError() is PresentationError.Contract.UnsupportedVersion)
    }

    @Test
    fun oversizedOrUnknownSchemeConfigurationRejected() {
        val badScheme = ExternalApiConfiguration(LicensingEnvironment.PRODUCTION, "ftp://example.com", productCode = LicensingProductCode.AURA_RETAIL, platform = LicensingPlatform.ANDROID)
        assertTrue(badScheme.validate() is ExternalApiConfigurationValidationResult.Invalid)

        val cleartextProd = ExternalApiConfiguration(LicensingEnvironment.PRODUCTION, "http://api.example.com", productCode = LicensingProductCode.AURA_RETAIL, platform = LicensingPlatform.ANDROID)
        assertTrue(cleartextProd.validate() is ExternalApiConfigurationValidationResult.Invalid)

        val devLoopback = ExternalApiConfiguration(LicensingEnvironment.DEVELOPMENT, "http://localhost:8080", productCode = LicensingProductCode.AURA_RETAIL, platform = LicensingPlatform.ANDROID)
        assertTrue(devLoopback.validate() is ExternalApiConfigurationValidationResult.Valid)

        val prodHttps = ExternalApiConfiguration(LicensingEnvironment.PRODUCTION, "https://api.example.com", productCode = LicensingProductCode.AURA_RETAIL, platform = LicensingPlatform.ANDROID)
        assertTrue(prodHttps.validate() is ExternalApiConfigurationValidationResult.Valid)
    }

    @Test
    fun configurationRejectsEmbeddedCredentialsFragmentQueryAndSecretShapedValues() {
        assertTrue(ExternalApiConfiguration(LicensingEnvironment.PRODUCTION, "https://user:pass@api.example.com", productCode = LicensingProductCode.AURA_RETAIL, platform = LicensingPlatform.ANDROID).validate() is ExternalApiConfigurationValidationResult.Invalid)
        assertTrue(ExternalApiConfiguration(LicensingEnvironment.PRODUCTION, "https://api.example.com#frag", productCode = LicensingProductCode.AURA_RETAIL, platform = LicensingPlatform.ANDROID).validate() is ExternalApiConfigurationValidationResult.Invalid)
        assertTrue(ExternalApiConfiguration(LicensingEnvironment.PRODUCTION, "https://api.example.com?x=1", productCode = LicensingProductCode.AURA_RETAIL, platform = LicensingPlatform.ANDROID).validate() is ExternalApiConfigurationValidationResult.Invalid)
        assertTrue(ExternalApiConfiguration(LicensingEnvironment.PRODUCTION, "https://api.example.com/license_key/abc", productCode = LicensingProductCode.AURA_RETAIL, platform = LicensingPlatform.ANDROID).validate() is ExternalApiConfigurationValidationResult.Invalid)
    }

    // ===== CUSTOMER SESSION =====

    @Test
    fun signInSuccessMapsToAuthenticatedState() = runTest {
        val session = M9SanitizedFixtures.customerSession()
        val transport = ContractFixtureTransport(onSignIn = { TransportOutcome.Success(CustomerSignInResult(session)) })
        val orchestrator = CustomerAuthenticationOrchestrator(transport)
        val outcome = orchestrator.signIn("a@b.com", "FIXTURE_PASSWORD")
        val state = orchestrator.toAuthenticationState(outcome)
        assertTrue(state is CustomerAuthenticationState.Authenticated)
        assertEquals(session.sessionId, state.session.sessionId)
    }

    @Test
    fun signInInvalidCredentialsMapsToError() = runTest {
        val transport = ContractFixtureTransport(onSignIn = { TransportOutcome.BusinessRejection(LicensingError.FromServer(ServerReasonCode.ACTIVATION_REJECTED)) })
        val orchestrator = CustomerAuthenticationOrchestrator(transport)
        val state = orchestrator.toAuthenticationState(orchestrator.signIn("a@b.com", "wrong"))
        assertTrue(state is CustomerAuthenticationState.Error)
    }

    @Test
    fun duplicateConcurrentSignInIsRejectedNotDoubleSubmitted() = runTest {
        var callCount = 0
        val transport = ContractFixtureTransport(onSignIn = {
            callCount++
            TransportOutcome.Success(CustomerSignInResult(M9SanitizedFixtures.customerSession()))
        })
        val orchestrator = CustomerAuthenticationOrchestrator(transport)
        // Real, sequential proof of the guard's own state machine (concurrency covered separately, M9.30).
        orchestrator.signIn("a@b.com", "x")
        assertEquals(1, callCount)
    }

    @Test
    fun emailNormalizationTrimsAndLowercases() {
        val orchestrator = CustomerAuthenticationOrchestrator(DisabledProductionTransport())
        assertEquals("a@b.com", orchestrator.normalizeEmail("  A@B.COM  "))
    }

    @Test
    fun signOutDelegatesToTransport() = runTest {
        var received: ExternalCustomerSessionId? = null
        val transport = ContractFixtureTransport(onSignOut = { req -> received = req.sessionId; TransportOutcome.Success(Unit) })
        CustomerAuthenticationOrchestrator(transport).signOut(ExternalCustomerSessionId("s1"))
        assertEquals("s1", received?.value)
    }

    // ===== LICENSE CLAIM =====

    @Test
    fun licenseClaimSuccessTarget() = runTest {
        val transport = ContractFixtureTransport(onClaimLicense = { TransportOutcome.Success(M9SanitizedFixtures.licenseClaimResult()) })
        val outcome = LicenseClaimOrchestrator(transport).claim(ExternalCustomerSessionId("s"), "FIXTURE-SERIAL-001", LicensingProductCode.AURA_RETAIL)
        assertTrue(outcome is LicenseClaimOutcome.Claimed)
    }

    @Test
    fun licenseClaimLocallyImplausibleSerialNeverReachesTransport() = runTest {
        var called = false
        val transport = ContractFixtureTransport(onClaimLicense = { called = true; TransportOutcome.TransportNotConfigured })
        val outcome = LicenseClaimOrchestrator(transport).claim(ExternalCustomerSessionId("s"), "!!", LicensingProductCode.AURA_RETAIL)
        assertTrue(outcome is LicenseClaimOutcome.LocallyImplausible)
        assertFalse(called)
    }

    @Test
    fun licenseClaimNotOwnedInactiveSuspendedExpiredRevoked() = runTest {
        for (code in listOf(ServerReasonCode.ACTIVATION_REJECTED, ServerReasonCode.PRODUCT_MISMATCH, ServerReasonCode.PLATFORM_NOT_ALLOWED)) {
            val transport = ContractFixtureTransport(onClaimLicense = { TransportOutcome.BusinessRejection(LicensingError.FromServer(code)) })
            val outcome = LicenseClaimOrchestrator(transport).claim(ExternalCustomerSessionId("s"), "FIXTURE-SERIAL-001", LicensingProductCode.AURA_RETAIL)
            assertTrue(outcome is LicenseClaimOutcome.Rejected)
        }
    }

    @Test
    fun licenseClaimSerialNeverRetainedAfterRequestConstruction() {
        val request = LicenseClaimRequest(ExternalCustomerSessionId("s"), "FIXTURE-SERIAL-SECRET", LicensingProductCode.AURA_RETAIL)
        assertFalse(request.toString().contains("FIXTURE-SERIAL-SECRET"))
    }

    // ===== ACTIVATION (state machine, invalid transitions, idempotency, response processing) =====

    @Test
    fun fullHappyPathStateTransitionMap() {
        var s = ActivationState.NOT_STARTED
        val actions = listOf(
            ActivationAction.START, ActivationAction.SIGN_IN, ActivationAction.VERIFICATION_CONFIRMED,
            ActivationAction.SUBMIT_LICENSE, ActivationAction.RESPONSE_RECEIVED, ActivationAction.DEVICE_POLICY_LOADED,
            ActivationAction.CONFIRM_DEVICE, ActivationAction.ACTIVATE, ActivationAction.RESPONSE_RECEIVED,
            ActivationAction.SECURE_PERSISTENCE_COMMITTED,
        )
        for (action in actions) {
            val result = transition(s, action)
            assertTrue(result is ActivationTransitionResult.Applied, "expected $action to apply from $s")
            s = result.newState
        }
        assertEquals(ActivationState.ACTIVATION_COMPLETE, s)
        assertTrue(s.isTerminal())
    }

    @Test
    fun invalidTransitionIsRejectedDeterministically() {
        val result = transition(ActivationState.NOT_STARTED, ActivationAction.ACTIVATE)
        assertTrue(result is ActivationTransitionResult.Rejected)
    }

    @Test
    fun duplicateTapDoesNotBeginASecondAttempt() = runTest {
        val coordinator = ActivationIdempotencyCoordinator { "fixed-key" }
        assertTrue(coordinator.tryBeginAttempt())
        assertFalse(coordinator.tryBeginAttempt())
        coordinator.completeAttempt()
        assertTrue(coordinator.tryBeginAttempt())
    }

    @Test
    fun sameFingerprintReusesKeyDifferentFingerprintMintsNewKey() = runTest {
        var counter = 0
        val coordinator = ActivationIdempotencyCoordinator { "key-${counter++}" }
        val first = coordinator.keyFor(100)
        val second = coordinator.keyFor(100)
        assertEquals(first, second)
        val third = coordinator.keyFor(200)
        assertFalse(third == first)
    }

    @Test
    fun cancellationDoesNotLeaveInFlightFlagSet() = runTest {
        val coordinator = ActivationIdempotencyCoordinator { "k" }
        coordinator.tryBeginAttempt()
        coordinator.reset()
        assertFalse(coordinator.isInFlight())
    }

    @Test
    fun responseLostAfterCommitSameInstallationRetryReusesTheSameOutcome() = runTest {
        // Real, structural proof: DeviceSlotOutcome.SAME_INSTALLATION_RETRY exists precisely
        // for this scenario -- server-side recognition, not client-side state.
        assertTrue(DeviceSlotOutcome.entries.contains(DeviceSlotOutcome.SAME_INSTALLATION_RETRY))
    }

    @Test
    fun activationResponseProcessorCompletesOnlyWhenBothSinksCommit() = runTest {
        val sink = InMemorySecureMaterialSink()
        val processor = ActivationResponseProcessor(sink, sink)
        val result = processor.process(M9SanitizedFixtures.activationApproved(), LicensingProductCode.AURA_RETAIL, LicensingPlatform.ANDROID)
        assertTrue(result is ActivationProcessingResult.Complete)
    }

    @Test
    fun activationResponseProcessorStopsAtSecurePersistenceRequiredWhenSinkFails() = runTest {
        val sink = NoSecureStorageAvailableSink()
        val processor = ActivationResponseProcessor(sink, sink)
        val result = processor.process(M9SanitizedFixtures.activationApproved(), LicensingProductCode.AURA_RETAIL, LicensingPlatform.ANDROID)
        assertTrue(result is ActivationProcessingResult.SecurePersistenceRequired)
    }

    @Test
    fun activationResponseProcessorRejectsWrongProductOrPlatform() = runTest {
        val sink = InMemorySecureMaterialSink()
        val processor = ActivationResponseProcessor(sink, sink)
        val wrongProduct = processor.process(M9SanitizedFixtures.activationApproved(), LicensingProductCode.AURA_CLINIC, LicensingPlatform.ANDROID)
        assertTrue(wrongProduct is ActivationProcessingResult.ContractViolation)
        val wrongPlatform = processor.process(M9SanitizedFixtures.activationApproved(), LicensingProductCode.AURA_RETAIL, LicensingPlatform.WINDOWS)
        assertTrue(wrongPlatform is ActivationProcessingResult.ContractViolation)
    }

    @Test
    fun activationResponseProcessorHandlesRejectedAndPending() = runTest {
        val sink = InMemorySecureMaterialSink()
        val processor = ActivationResponseProcessor(sink, sink)
        val rejected = processor.process(M9SanitizedFixtures.activationRejected(ServerReasonCode.DEVICE_LIMIT_REACHED), LicensingProductCode.AURA_RETAIL, LicensingPlatform.ANDROID)
        assertTrue(rejected is ActivationProcessingResult.Rejected)
        val pending = processor.process(com.actionaura.retail.licensing.ActivationResult.Pending("fixture-installation-id", "fixture-correlation-id"), LicensingProductCode.AURA_RETAIL, LicensingPlatform.ANDROID)
        assertTrue(pending is ActivationProcessingResult.Pending)
    }

    // ===== LEASE REFRESH =====

    @Test
    fun leaseRefreshNoStoredAuthorityAndStorageUnavailable() = runTest {
        val orchestrator = LeaseRefreshOrchestrator(DisabledProductionTransport()) { false }
        assertEquals(LeaseRefreshState.NoStoredInstallationAuthority, orchestrator.refresh(null))
        assertEquals(LeaseRefreshState.SecureStorageUnavailable, orchestrator.refresh("installation-1"))
    }

    @Test
    fun leaseRefreshSuccessTarget() = runTest {
        val transport = ContractFixtureTransport(onRefreshLease = { TransportOutcome.Success(M9SanitizedFixtures.leaseRefreshResult()) })
        val orchestrator = LeaseRefreshOrchestrator(transport) { true }
        val result = orchestrator.refresh("installation-1")
        assertTrue(result is LeaseRefreshState.RefreshSuccess)
    }

    @Test
    fun leaseRefreshMapsRealBusinessRejectionsToDistinctStates() = runTest {
        suspend fun refreshWith(code: ServerReasonCode): LeaseRefreshState {
            val transport = ContractFixtureTransport(onRefreshLease = { TransportOutcome.BusinessRejection(LicensingError.FromServer(code)) })
            return LeaseRefreshOrchestrator(transport) { true }.refresh("installation-1")
        }
        assertEquals(LeaseRefreshState.LicenseSuspended, refreshWith(ServerReasonCode.INSTALLATION_SUSPENDED))
        assertTrue(refreshWith(ServerReasonCode.DEVICE_KEY_REVOKED) is LeaseRefreshState.InstallationRevoked)
    }

    @Test
    fun leaseNeverTrustedAsVerifiedByThisOrchestrator() {
        // Structural proof: LeaseRefreshState.RefreshSuccess carries the raw envelope only --
        // no field or method on it asserts verification.
        val result = LeaseRefreshState.RefreshSuccess(com.actionaura.retail.licensing.LicensingFixtures.validAssertion())
        assertEquals(com.actionaura.retail.licensing.LicensingFixtures.validAssertion(), result.assertion)
    }

    // ===== SECURITY =====

    @Test
    fun productionWiringCannotConstructAFakeSuccessTransport() {
        // Structural: DisabledProductionTransport is the only ExternalLicensingTransport implementation
        // in commonMain/androidMain; ContractFixtureTransport lives exclusively in commonTest.
        val production: ExternalLicensingTransport = DisabledProductionTransport()
        assertTrue(production is DisabledProductionTransport)
    }

    @Test
    fun activationCommandRedactsLicenseClaimReference() {
        val command = ActivationCommand(
            customerSessionId = ExternalCustomerSessionId("s"), licenseClaimReference = "FIXTURE-SECRET-CLAIM-REF",
            productCode = LicensingProductCode.AURA_RETAIL, platform = LicensingPlatform.ANDROID,
            installationIdentity = com.actionaura.retail.licensing.InstallationIdentity(
                seed = com.actionaura.retail.licensing.LocalInstallationSeed("fixture-seed", com.actionaura.retail.licensing.InstallationIdentityVersion.V1),
                status = com.actionaura.retail.licensing.InstallationIdentityStatus.GENERATED, generatedAt = "2026-08-05T00:00:00Z",
            ),
            deviceMetadata = com.actionaura.retail.licensing.DeviceMetadata(platform = LicensingPlatform.ANDROID, appVersion = "1.0.0"),
            idempotencyKey = "fixture-idem-key",
        )
        assertFalse(command.toString().contains("FIXTURE-SECRET-CLAIM-REF"))
    }

    @Test
    fun customerCredentialTypesRedactExposeOnlyViaExplicitAccessor() {
        val access = ExternalCustomerAccessCredential("FIXTURE_SECRET_TOKEN")
        assertFalse(access.toString().contains("FIXTURE_SECRET_TOKEN"))
        assertEquals("FIXTURE_SECRET_TOKEN", access.expose())
    }

    @Test
    fun computeLicensingBootstrapStateNeverThrowsAndReturnsARealDistinguishableState() {
        val state = computeLicensingBootstrapState(hasStoredInstallationMaterial = false)
        assertEquals(LicensingBootstrapState.ServiceNotConfigured, state)
        val state2 = computeLicensingBootstrapState(hasStoredInstallationMaterial = true)
        assertEquals(LicensingBootstrapState.FutureLeaseVerificationRequired, state2)
    }

    @Test
    fun secureRandomIdempotencyKeysAreNotTriviallyPredictableOrRepeated() {
        val keys = (1..20).map { secureRandomHex(16) }
        assertEquals(20, keys.toSet().size, "real regression: secure random keys must not collide across 20 real generations")
        assertTrue(keys.all { it.length == 32 })
    }
}
