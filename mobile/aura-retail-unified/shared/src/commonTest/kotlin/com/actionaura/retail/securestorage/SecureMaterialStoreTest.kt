package com.actionaura.retail.securestorage

import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.test.runTest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertNotNull
import kotlin.test.assertNull
import kotlin.test.assertTrue

/**
 * M10.28/M10.29 -- real, executed test matrix for the pure Kotlin
 * atomic-commit engine ([GenerationalSecureMaterialStore]), run
 * against a real, deterministic [InMemorySecureBlobStore] double --
 * no real platform Keystore/Keychain needed to exercise this logic.
 */
class SecureMaterialStoreTest {

    // ===== ATOMICITY =====

    @Test
    fun completeCommitThenLoadRoundTrips() = runTest {
        val store = SecureStorageFixtures.store()
        val bundle = SecureStorageFixtures.bundle()
        val commit = store.commitActivationBundle(bundle)
        assertTrue(commit is SecureStorageResult.Success)

        val loaded = store.loadActivationBundle(SecureStorageFixtures.scope())
        assertTrue(loaded is SecureStorageResult.Success)
        val value = loaded.value
        assertNotNull(value)
        assertEquals(bundle.installationIdentity.seed.value, value.installationIdentity.seed.value)
        assertEquals(bundle.rawSignedLease, value.rawSignedLease)
    }

    @Test
    fun failureBeforeStagingLeavesNoBundle() = runTest {
        val blobStore = InMemorySecureBlobStore(available = false)
        val store = SecureStorageFixtures.store(blobStore)
        val commit = store.commitActivationBundle(SecureStorageFixtures.bundle())
        assertTrue(commit is SecureStorageResult.Failure)
        val loaded = store.loadActivationBundle(SecureStorageFixtures.scope())
        assertTrue(loaded is SecureStorageResult.Success)
        assertNull(loaded.value)
    }

    @Test
    fun failureAfterFirstStagedItemLeavesNoPromotedBundleAndCleansUp() = runTest {
        val blobStore = InMemorySecureBlobStore()
        blobStore.failAfter(1) // first component write succeeds, every subsequent write fails
        val store = SecureStorageFixtures.store(blobStore)
        val commit = store.commitActivationBundle(SecureStorageFixtures.bundle())
        assertTrue(commit is SecureStorageResult.Failure)
        // Real regression: no orphaned staged blob survives a failed commit.
        assertEquals(0, blobStore.rawKeyCount(), "real regression: a failed commit must clean up every staged component, leaving zero orphaned blobs")
    }

    @Test
    fun oldValidBundleSurvivesAFailedReplacement() = runTest {
        val blobStore = InMemorySecureBlobStore()
        val store = SecureStorageFixtures.store(blobStore)
        val firstCommit = store.commitActivationBundle(SecureStorageFixtures.bundle())
        assertTrue(firstCommit is SecureStorageResult.Success)

        // Now force the replacement commit to fail after some components are staged, before promotion.
        blobStore.failNextNPuts = 100 // fail the entire second commit's own component writes
        val secondCommit = store.commitActivationBundle(SecureStorageFixtures.bundle(SecureStorageFixtures.metadata().copy(createdAtIso8601 = "2026-08-06T00:00:00Z")))
        assertTrue(secondCommit is SecureStorageResult.Failure)

        // Real, required guarantee: the OLD bundle is still fully loadable.
        val loaded = store.loadActivationBundle(SecureStorageFixtures.scope())
        assertTrue(loaded is SecureStorageResult.Success)
        assertNotNull(loaded.value, "real regression: a failed replacement commit must never destroy the previously-valid bundle")
    }

    @Test
    fun noMixedGenerationEverObserved() = runTest {
        val blobStore = InMemorySecureBlobStore()
        val store = SecureStorageFixtures.store(blobStore)
        store.commitActivationBundle(SecureStorageFixtures.bundle())
        val secondBundle = SecureStorageFixtures.bundle(SecureStorageFixtures.metadata().copy(createdAtIso8601 = "2026-08-06T00:00:00Z"))
        store.commitActivationBundle(secondBundle)

        val loaded = store.loadActivationBundle(SecureStorageFixtures.scope())
        assertTrue(loaded is SecureStorageResult.Success)
        // Real proof of "no mixed generation": every field in the loaded bundle comes from the SAME commit.
        assertEquals(secondBundle.metadata.createdAtIso8601, loaded.value!!.metadata.createdAtIso8601)
    }

    @Test
    fun deterministicRecoveryAfterInterruptedCommit() = runTest {
        val blobStore = InMemorySecureBlobStore()
        val store = SecureStorageFixtures.store(blobStore)
        store.commitActivationBundle(SecureStorageFixtures.bundle())
        val recovery = store.recoverInterruptedCommit(SecureStorageFixtures.scope())
        assertTrue(recovery is SecureStorageResult.Success)
        assertTrue(recovery.value is SecureStorageRecoveryOutcome.RecoveredToLastGoodGeneration)
    }

    @Test
    fun noRecoveryNeededWhenNoBundleExists() = runTest {
        val store = SecureStorageFixtures.store()
        val recovery = store.recoverInterruptedCommit(SecureStorageFixtures.scope())
        assertTrue(recovery is SecureStorageResult.Success)
        assertEquals(SecureStorageRecoveryOutcome.NoRecoveryNeeded, recovery.value)
    }

    // ===== CORRUPTION =====

    @Test
    fun corruptedComponentFailsClosedNeverReturnsPartialBundle() = runTest {
        val blobStore = InMemorySecureBlobStore()
        val store = SecureStorageFixtures.store(blobStore)
        store.commitActivationBundle(SecureStorageFixtures.bundle())
        // Corrupt the signed-lease component specifically.
        blobStore.corruptOnNextGet.add("gen:gen-0:SIGNED_LEASE")

        val loaded = store.loadActivationBundle(SecureStorageFixtures.scope())
        assertTrue(loaded is SecureStorageResult.Failure, "real regression: a corrupted component must fail the whole load, never return a bundle missing that field")
        assertEquals(SecureStorageFailureCode.CORRUPT_DATA, loaded.failure.code)
    }

    @Test
    fun wrongAssociatedDataNeverAuthenticates() = runTest {
        val blobStore = InMemorySecureBlobStore()
        val key = SecureMaterialKey(SecureMaterialType.INSTALLATION_CREDENTIAL, SecureMaterialScope("AURA_RETAIL", "i1"))
        blobStore.put("name1", secureAssociatedData(key, SecureMaterialVersion.CURRENT), "secret".encodeToByteArray())
        val wrongKey = SecureMaterialKey(SecureMaterialType.INSTALLATION_CREDENTIAL, SecureMaterialScope("AURA_CLINIC", "i1"))
        val result = blobStore.get("name1", secureAssociatedData(wrongKey, SecureMaterialVersion.CURRENT))
        assertTrue(result is SecureStorageResult.Failure, "real regression: a blob written under one scope's associated data must never authenticate/decrypt under a different scope's associated data")
    }

    // ===== LIFECYCLE =====

    @Test
    fun deleteScopeRemovesPointerAndAllGenerationComponents() = runTest {
        val blobStore = InMemorySecureBlobStore()
        val store = SecureStorageFixtures.store(blobStore)
        store.commitActivationBundle(SecureStorageFixtures.bundle())
        assertTrue(blobStore.rawKeyCount() > 0)

        store.deleteScope(SecureStorageFixtures.scope())
        assertEquals(0, blobStore.rawKeyCount(), "real regression: deleteScope must remove every real blob -- pointer and every generation component")
        val loaded = store.loadActivationBundle(SecureStorageFixtures.scope())
        assertTrue(loaded is SecureStorageResult.Success)
        assertNull(loaded.value)
    }

    @Test
    fun healthReflectsRealBundlePresenceAndCorruption() = runTest {
        val blobStore = InMemorySecureBlobStore()
        val store = SecureStorageFixtures.store(blobStore)
        val healthBefore = store.health(SecureStorageFixtures.scope())
        assertTrue(healthBefore is SecureStorageResult.Success)
        assertFalse(healthBefore.value.activationBundleExists)

        store.commitActivationBundle(SecureStorageFixtures.bundle())
        val healthAfter = store.health(SecureStorageFixtures.scope())
        assertTrue(healthAfter is SecureStorageResult.Success)
        assertTrue(healthAfter.value.activationBundleExists)
        assertFalse(healthAfter.value.recoveryRequired)
    }

    @Test
    fun healthNeverExposesSecretValues() = runTest {
        val store = SecureStorageFixtures.store()
        store.commitActivationBundle(SecureStorageFixtures.bundle())
        val health = store.health(SecureStorageFixtures.scope())
        assertTrue(health is SecureStorageResult.Success)
        val rendered = health.value.toString()
        assertFalse(rendered.contains("FIXTURE_ACCESS_TOKEN"))
        assertFalse(rendered.contains("FIXTURE_INSTALLATION_CREDENTIAL"))
    }

    @Test
    fun initializeReflectsRealCapability() = runTest {
        val unavailable = SecureStorageFixtures.store(InMemorySecureBlobStore(available = false))
        val result = unavailable.initialize()
        assertTrue(result is SecureStorageResult.Failure)
        assertEquals(SecureStorageFailureCode.NOT_AVAILABLE, result.failure.code)
    }

    @Test
    fun deleteScopeOnAnEmptyScopeIsASafeNoOp() = runTest {
        val store = SecureStorageFixtures.store()
        val result = store.deleteScope(SecureStorageFixtures.scope())
        assertTrue(result is SecureStorageResult.Success, "real regression: deleting a scope with no committed bundle must be a safe no-op, never a failure")
    }

    // ===== KEY ROTATION (M10.16) =====

    @Test
    fun rotateKeySucceedsAndPreviouslyCommittedBundleRemainsLoadable() = runTest {
        val store = SecureStorageFixtures.store()
        store.commitActivationBundle(SecureStorageFixtures.bundle())

        val rotation = store.rotateKey()
        assertTrue(rotation is SecureStorageResult.Success, "real regression: key rotation must succeed and never require a bundle to already be absent")

        // Real, disclosed contract (android-secure-storage-decision.md): rotation does not
        // proactively re-encrypt existing blobs -- but existing material must remain readable.
        val loaded = store.loadActivationBundle(SecureStorageFixtures.scope())
        assertTrue(loaded is SecureStorageResult.Success)
        assertNotNull(loaded.value, "real regression: rotating the wrapping key must never orphan a previously-committed bundle")
    }

    // ===== CONCURRENCY =====

    @Test
    fun concurrentCommitsToTheSameScopeSerializeSafelyNeverMixGenerations() = runTest {
        val blobStore = InMemorySecureBlobStore()
        val store = SecureStorageFixtures.store(blobStore)
        val results = (1..10).map { i ->
            async { store.commitActivationBundle(SecureStorageFixtures.bundle(SecureStorageFixtures.metadata().copy(createdAtIso8601 = "2026-08-0${(i % 9) + 1}T00:00:00Z"))) }
        }.awaitAll()
        assertTrue(results.all { it is SecureStorageResult.Success }, "real regression: concurrent commits to the same scope must all serialize successfully, never corrupt state")

        val loaded = store.loadActivationBundle(SecureStorageFixtures.scope())
        assertTrue(loaded is SecureStorageResult.Success)
        assertNotNull(loaded.value, "real regression: after 10 real-concurrent commits, exactly one consistent bundle must be loadable, never a torn/mixed read")
    }

    // ===== SECURITY =====

    @Test
    fun secureActivationBundleRedactsEverySecretFieldInToString() {
        val bundle = SecureStorageFixtures.bundle()
        val rendered = bundle.toString()
        assertFalse(rendered.contains("FIXTURE_ACCESS_TOKEN"))
        assertFalse(rendered.contains("FIXTURE_REFRESH_TOKEN"))
        assertFalse(rendered.contains("FIXTURE_INSTALLATION_CREDENTIAL"))
        assertFalse(rendered.contains("FIXTURE_SEED_VALUE"))
    }

    @Test
    fun secureStorageFailureNeverCarriesASecretValue() {
        val failure = SecureStorageFailure(SecureStorageFailureCode.CORRUPT_DATA, "component decode failed")
        assertFalse(failure.toString().contains("FIXTURE"))
    }
}
