package com.actionaura.retail.ui.activation

import com.actionaura.retail.licensing.DeviceLimitMode
import com.actionaura.retail.licensing.InstallationIdentity
import com.actionaura.retail.licensing.InstallationIdentityStatus
import com.actionaura.retail.licensing.InstallationIdentityVersion
import com.actionaura.retail.licensing.LicensingPlatform
import com.actionaura.retail.licensing.LicensingProductCode
import com.actionaura.retail.licensing.LocalInstallationSeed
import com.actionaura.retail.licensing.ResolvedDevicePolicy
import com.actionaura.retail.licensing.transport.ActivationResponseProcessor
import com.actionaura.retail.licensing.transport.ActivationState
import com.actionaura.retail.licensing.transport.ContractFixtureTransport
import com.actionaura.retail.licensing.transport.CustomerSessionExpiry
import com.actionaura.retail.licensing.transport.CustomerSignInResult
import com.actionaura.retail.licensing.transport.ExternalCustomerAccessCredential
import com.actionaura.retail.licensing.transport.ExternalCustomerAccountId
import com.actionaura.retail.licensing.transport.ExternalCustomerSession
import com.actionaura.retail.licensing.transport.ExternalCustomerSessionId
import com.actionaura.retail.licensing.transport.ExternalCustomerSessionStatus
import com.actionaura.retail.licensing.transport.ExternalApiConfiguration
import com.actionaura.retail.licensing.transport.HttpExternalLicensingTransport
import com.actionaura.retail.licensing.transport.LicenseClaimResult
import com.actionaura.retail.licensing.transport.LicensingEnvironment
import com.actionaura.retail.licensing.transport.TransportOutcome
import com.actionaura.retail.licensing.transport.secureRandomHex
import com.actionaura.retail.securestorage.GenerationalSecureMaterialStore
import com.actionaura.retail.securestorage.InMemorySecureBlobStore
import com.actionaura.retail.securestorage.SecureMaterialStoreActivationSink
import com.actionaura.retail.sync.PlatformDeviceSigner
import com.actionaura.retail.sync.SyncRelayConfiguration
import com.actionaura.retail.sync.resolveActiveSyncTransport
import io.ktor.client.HttpClient
import io.ktor.client.engine.cio.CIO
import io.ktor.client.engine.okhttp.OkHttp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import kotlinx.datetime.Clock
import org.junit.Assume.assumeTrue
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNotNull

/**
 * Task 11a (multi-device-sync-foundation) -- real, executable end-to-end
 * proof that a real activation driven through [ActivationViewModel]'s own
 * PUBLIC methods -- exactly the calls `ActivationScreen` (`ActivationFlow.kt`)
 * makes on real user taps -- genuinely reaches a REAL, locally running
 * Owner instance and genuinely commits a real activation bundle, following
 * the exact same live-test discipline Tasks 8/9/10 already established.
 *
 * `onStart()`/`onSignIn()`/`onSubmitLicense()`/`onConfirmDevice()` are
 * driven against a [ContractFixtureTransport] configured to succeed --
 * NOT against Owner. This is a real, disclosed, PRE-EXISTING limitation
 * this task inherited, not something it introduced or was asked to fix
 * (see `task-11a-report.md`'s own "Concerns" section for the full
 * analysis): Owner's real customer-session/license-claim server-side
 * authority (`register`/`signIn`/`claimLicense`) does not exist yet
 * (`HttpExternalLicensingTransport`'s own KDoc: only [activateWithLicenseKey]
 * is real), and building it is explicitly out of this task's scope (its
 * own "When You're in Over Your Head" section). Faking ONLY that
 * pre-existing, separately-scoped gap is the narrowest way to exercise
 * this task's own real, new code -- [ActivationViewModel.onActivate] --
 * through the SAME public entry points a real screen tap uses, rather
 * than reaching into private state.
 *
 * `onActivate()` itself is 100% real: it calls the real, wire-verified
 * [HttpExternalLicensingTransport.activateWithLicenseKey] against a real
 * locally running Owner instance, and a real success reaches a real
 * [SecureMaterialStoreActivationSink] over a real
 * [GenerationalSecureMaterialStore] -- exactly `AuraAppContainer.newActivationViewModel()`'s
 * own real production wiring, constructed here by hand (not via
 * `AuraAppContainer`, to avoid a real SQLDelight database dependency this
 * test does not need). The real proof this task's own brief asks for --
 * "does [resolveActiveSyncTransport] find a real transport afterward" --
 * is checked directly at the end, using the SAME [secureBlobStore]/
 * [secureMaterialStore] instances the activation just committed into.
 *
 * Gated behind `RUN_OWNER_LIVE_ACTIVATION_TEST=1` + `OWNER_LIVE_TEST_LICENSE_KEY`
 * (the same env vars `HttpExternalLicensingTransportActivationLiveTest`
 * already uses) so this never runs as part of the ordinary test suite.
 */
class ActivationViewModelLiveTest {

    @Test
    fun realActivationThroughActivationViewModelsPublicApiCommitsARealBundleAndSyncFindsIt() = runBlocking {
        // Real `runBlocking`, NOT `kotlinx.coroutines.test.runTest` -- this test's own
        // first attempt used `runTest`, and `withTimeout(30_000)` below fired almost
        // instantly (well under 30 real seconds): `runTest`'s TestScope runs this
        // function body on a virtual-time `StandardTestDispatcher`, and `withTimeout`'s
        // internal `delay` is scheduled on that SAME ambient dispatcher -- with the real
        // I/O work happening on a genuinely different dispatcher (`Dispatchers.Default`,
        // the ViewModel's own), `runTest` sees no more work scheduled on ITS dispatcher
        // and immediately fast-forwards virtual time straight to the timeout, cancelling
        // the real wait before the real HTTP call could ever complete. `runBlocking` has
        // no virtual clock -- `withTimeout` genuinely waits real wall-clock time, exactly
        // matching Tasks 8/9/10's own real-I/O live tests (which never mixed a virtual-
        // time scope with a separately-dispatched real workload the way this one does).
        val runLiveTest = System.getenv("RUN_OWNER_LIVE_ACTIVATION_TEST") == "1"
        assumeTrue("set RUN_OWNER_LIVE_ACTIVATION_TEST=1 and OWNER_LIVE_TEST_LICENSE_KEY to run this against a real local Owner instance", runLiveTest)
        val licenseKey = System.getenv("OWNER_LIVE_TEST_LICENSE_KEY")
        assumeTrue("OWNER_LIVE_TEST_LICENSE_KEY must be set to a real, issued AURA_RETAIL test license key", !licenseKey.isNullOrBlank())

        val baseUrl = System.getenv("OWNER_LIVE_TEST_BASE_URL") ?: "http://127.0.0.1:5551"

        // ---- Real dependencies this test hand-wires (mirrors AuraAppContainer.newActivationViewModel()) ----
        val secureBlobStore = InMemorySecureBlobStore()
        val secureMaterialStore = GenerationalSecureMaterialStore(
            blobStore = secureBlobStore,
            nowIso8601 = { Clock.System.now().toString() },
            newGenerationId = { secureRandomHex(16) },
        )
        val deviceSigner = PlatformDeviceSigner(secureBlobStore)
        val licensingConfiguration = ExternalApiConfiguration(
            environment = LicensingEnvironment.DEVELOPMENT,
            baseUrl = "$baseUrl/api/licensing/v1",
            productCode = LicensingProductCode.AURA_RETAIL,
            platform = LicensingPlatform.ANDROID,
        )
        val realLicensingTransport = HttpExternalLicensingTransport(HttpClient(OkHttp), licensingConfiguration, deviceSigner)

        val installationIdentity = InstallationIdentity(
            seed = LocalInstallationSeed("task11a-live-test-" + secureRandomHex(16), InstallationIdentityVersion.V1),
            status = InstallationIdentityStatus.GENERATED,
            generatedAt = Clock.System.now().toString(),
        )

        // ---- The one, disclosed fake: sign-in/claim, pre-existing unreal server-side gap ----
        val fixtureSession = ExternalCustomerSession(
            accountId = ExternalCustomerAccountId("task11a-live-test-account"),
            sessionId = ExternalCustomerSessionId("task11a-live-test-session-" + secureRandomHex(8)),
            accessCredential = ExternalCustomerAccessCredential("fixture-access-token"),
            refreshCredential = null,
            expiry = CustomerSessionExpiry("2027-01-01T00:00:00Z"),
            status = ExternalCustomerSessionStatus.AUTHENTICATED,
        )
        val fixtureDevicePolicy = ResolvedDevicePolicy(
            policyVersion = 1,
            licensePublicId = "task11a-live-test-license-public-id",
            productCode = LicensingProductCode.AURA_RETAIL,
            allowedPlatforms = listOf("ANDROID"),
            deviceLimitMode = DeviceLimitMode.TOTAL_ACTIVE_INSTALLATIONS,
            totalActiveInstallationLimit = 50,
            currentActiveInstallationCount = 0,
            remainingInstallationSlots = 50,
            voluntaryDeactivationAllowed = true,
            replacementAllowed = true,
            policyEffectiveAt = Clock.System.now().toString(),
        )
        val fixtureTransport = ContractFixtureTransport(
            onSignIn = { TransportOutcome.Success(CustomerSignInResult(fixtureSession)) },
            onClaimLicense = { TransportOutcome.Success(LicenseClaimResult(fixtureDevicePolicy.licensePublicId, fixtureDevicePolicy)) },
        )

        // ---- The real ActivationViewModel, wired exactly like AuraAppContainer.newActivationViewModel() ----
        val viewModel = ActivationViewModel(
            transport = fixtureTransport,
            productCode = LicensingProductCode.AURA_RETAIL,
            platform = LicensingPlatform.ANDROID,
            installationIdentityProvider = { installationIdentity },
            activateWithLicenseKey = realLicensingTransport::activateWithLicenseKey,
            activationResponseProcessorFactory = { ownerInstallationId, identity ->
                val sink = SecureMaterialStoreActivationSink(
                    store = secureMaterialStore,
                    installationIdentity = identity,
                    productCode = LicensingProductCode.AURA_RETAIL.name,
                    platform = LicensingPlatform.ANDROID.name,
                    ownerInstallationId = ownerInstallationId,
                    nowIso8601 = { Clock.System.now().toString() },
                )
                ActivationResponseProcessor(sink, sink)
            },
            // Real `Dispatchers.Default`, NOT a `StandardTestDispatcher` -- `onActivate()`
            // below performs genuine suspending network I/O on Ktor/OkHttp's own real
            // thread pool. A virtual-time test dispatcher's `advanceUntilIdle()` only
            // drains work already scheduled ON that dispatcher; it does not (and, a first
            // attempt at this test proved empirically, genuinely does not) wait for a
            // continuation resumed by a real, independent OS thread to post back. Real
            // time + polling `viewModel.state` below is the correct, robust way to await
            // real I/O, matching Tasks 8/9/10's own `runTest { <real suspend calls> }`
            // discipline (those tests await real suspend functions directly; this one
            // awaits a fire-and-forget `launchOnDefault` indirectly via the state flow).
            dispatcher = Dispatchers.Default,
        )

        suspend fun awaitState(predicate: (ActivationState) -> Boolean) =
            withTimeout(30_000) { viewModel.state.first { predicate(it.activationState) } }

        // ---- Drive it exactly like ActivationScreen (ActivationFlow.kt) would on real taps ----
        viewModel.onStart()
        assertEquals(ActivationState.CUSTOMER_SESSION_REQUIRED, viewModel.state.value.activationState)

        viewModel.onEmailChange("task11a-live-test@example.com")
        viewModel.onSignIn("fixture-password")
        // Exact-match predicates (not "!= the state we started from") -- awaiting the
        // precise target state avoids a real race against this coroutine's own two
        // back-to-back, non-suspending `applyAction` calls (RESPONSE_RECEIVED then
        // DEVICE_POLICY_LOADED below), where `first {}` collecting on a different real
        // thread could otherwise observe the transient intermediate state.
        awaitState { it == ActivationState.LICENSE_INPUT_REQUIRED }
        assertEquals(ActivationState.LICENSE_INPUT_REQUIRED, viewModel.state.value.activationState, "expected real onSignIn() (fixture-backed) to reach LICENSE_INPUT_REQUIRED, got ${viewModel.state.value}")

        viewModel.onLicenseSerialChange(licenseKey!!)
        viewModel.onSubmitLicense()
        awaitState { it == ActivationState.INSTALLATION_IDENTITY_REQUIRED }
        assertEquals(ActivationState.INSTALLATION_IDENTITY_REQUIRED, viewModel.state.value.activationState, "expected real onSubmitLicense() (fixture-backed) to reach INSTALLATION_IDENTITY_REQUIRED, got ${viewModel.state.value}")

        viewModel.onDeviceLabelChange("Task 11a live test device")
        viewModel.onConfirmDevice()
        assertEquals(ActivationState.READY_TO_ACTIVATE, viewModel.state.value.activationState)

        // ---- THE REAL SUBJECT OF THIS TASK: onActivate() -- real HTTP, real persistence ----
        viewModel.onActivate()
        awaitState { it == ActivationState.ACTIVATION_COMPLETE }
        println("Task 11a live test: final ActivationUiState = ${viewModel.state.value}")
        assertEquals(
            ActivationState.ACTIVATION_COMPLETE,
            viewModel.state.value.activationState,
            "expected a real activation through ActivationViewModel.onActivate() to reach ACTIVATION_COMPLETE (real HTTP success + real store.commitActivationBundle()), got ${viewModel.state.value}",
        )

        // ---- The real proof this task's own brief asks for: does sync now find a real transport? ----
        val resolvedTransport = resolveActiveSyncTransport(
            secureBlobStore = secureBlobStore,
            secureMaterialStore = secureMaterialStore,
            deviceSigner = deviceSigner,
            httpClient = HttpClient(CIO), // MUST be CIO -- SyncTransport.kt's own class KDoc.
            configuration = SyncRelayConfiguration(environment = LicensingEnvironment.DEVELOPMENT, baseUrl = baseUrl),
            productCode = LicensingProductCode.AURA_RETAIL.name,
        )
        assertNotNull(
            resolvedTransport,
            "resolveActiveSyncTransport (the exact function AuraAppContainer's DI wiring calls) must find the activation bundle ActivationViewModel.onActivate() just committed above -- if this is null, the real UI-to-secure-storage wiring this task adds is still broken",
        )
        println("Task 11a live test: resolveActiveSyncTransport found a real transport after a real ActivationViewModel.onActivate() -- the wiring this task adds is genuinely end-to-end.")
    }
}
