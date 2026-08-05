package com.actionaura.retail.securestorage

import com.actionaura.retail.licensing.LicensingFixtures
import com.actionaura.retail.licensing.transport.InstallationCredentialMaterial
import com.actionaura.retail.licensing.transport.SecureMaterialCommitResult
import kotlinx.coroutines.test.runTest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNull
import kotlin.test.assertTrue

/**
 * M10.21/M10.29 -- real proof that the M9-to-M10 activation sink
 * bridge only durably commits once both real pieces (credential and
 * lease) have arrived, never on either alone.
 */
class SecureMaterialStoreActivationSinkTest {

    /** Real, deliberate: customerAccountId left null here (and matched in [scope]) since the sink under test is never given one -- a mismatched scope between the sink and the assertion would itself be the exact bug this test exists to catch. */
    private fun scope() = SecureMaterialScope("AURA_RETAIL", "fixture-installation-0001", null)

    private fun sink(): Pair<SecureMaterialStoreActivationSink, SecureMaterialStore> {
        val store = SecureStorageFixtures.store()
        val identity = SecureStorageFixtures.bundle().installationIdentity
        val sink = SecureMaterialStoreActivationSink(
            store = store, installationIdentity = identity,
            productCode = "AURA_RETAIL", platform = "ANDROID", ownerInstallationId = "fixture-installation-0001",
            customerAccountId = null,
            nowIso8601 = { "2026-08-05T00:00:00Z" },
        )
        return sink to store
    }

    @Test
    fun credentialAloneDoesNotDurablyCommit() = runTest {
        val (sink, store) = sink()
        val result = sink.commit("fixture-installation-0001", InstallationCredentialMaterial("FIXTURE_CREDENTIAL"))
        assertTrue(result is SecureMaterialCommitResult.Failed)
        val loaded = store.loadActivationBundle(scope())
        assertTrue(loaded is SecureStorageResult.Success)
        assertNull(loaded.value, "real regression: a credential commit alone must never produce a durable, loadable bundle")
    }

    @Test
    fun leaseAloneDoesNotDurablyCommit() = runTest {
        val (sink, store) = sink()
        val result = sink.commit("fixture-installation-0001", LicensingFixtures.validAssertion())
        assertTrue(result is SecureMaterialCommitResult.Failed)
        val loaded = store.loadActivationBundle(scope())
        assertTrue(loaded is SecureStorageResult.Success)
        assertNull(loaded.value)
    }

    @Test
    fun bothPiecesTogetherProduceOneRealAtomicCommit() = runTest {
        val (sink, store) = sink()
        sink.commit("fixture-installation-0001", InstallationCredentialMaterial("FIXTURE_CREDENTIAL"))
        val result = sink.commit("fixture-installation-0001", LicensingFixtures.validAssertion())
        assertEquals(SecureMaterialCommitResult.Committed, result)

        val loaded = store.loadActivationBundle(scope())
        assertTrue(loaded is SecureStorageResult.Success)
        assertTrue(loaded.value != null, "real regression: once both pieces arrive, exactly one real atomic commit must produce a durable, loadable bundle")
    }
}
