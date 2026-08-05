package com.actionaura.retail.securestorage

import com.actionaura.retail.licensing.InstallationIdentity
import com.actionaura.retail.licensing.InstallationIdentityStatus
import com.actionaura.retail.licensing.InstallationIdentityVersion
import com.actionaura.retail.licensing.LocalInstallationSeed
import com.actionaura.retail.licensing.SignedAssertionEnvelope
import com.actionaura.retail.licensing.transport.ExternalCustomerAccessCredential
import com.actionaura.retail.licensing.transport.ExternalCustomerRefreshCredential
import com.actionaura.retail.licensing.transport.InstallationCredentialMaterial
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json

/**
 * M10.6 -- the real atomic-commit authority
 * (`secure-material-atomic-commit.md`). Pure Kotlin, testable without
 * any real platform Keystore/Keychain (the [SecureBlobStore] it runs
 * on may be a real platform adapter or a real in-memory test double --
 * this class's own atomicity/generation/recovery logic is identical
 * either way).
 *
 * Real generation/pointer strategy (per the checkpoint's own required
 * "generation/pointer or equivalent proven strategy"):
 * 1. Write every real bundle component under a fresh generation id.
 * 2. Read every written component back and verify it decrypts.
 * 3. Only then write the pointer blob (one single blob put) to the new
 *    generation id -- this is the real atomic "promotion" moment; the
 *    old generation remains the pointer's target, and therefore fully
 *    usable, until this one write succeeds.
 * 4. Delete the now-superseded generation's blobs.
 *
 * A failure at any point before step 3 leaves the old generation
 * still pointed-to and fully readable -- "old valid bundle remains
 * usable when replacement fails" (M10.6's own required guarantee).
 */
class GenerationalSecureMaterialStore(
    private val blobStore: SecureBlobStore,
    private val nowIso8601: () -> String,
    private val newGenerationId: () -> String,
) : SecureMaterialStore {

    private val mutex = Mutex()
    private val json = Json { ignoreUnknownKeys = true; encodeDefaults = true }

    override suspend fun initialize(): SecureStorageResult<Unit> {
        val capability = blobStore.capability()
        return if (capability.available) SecureStorageResult.Success(Unit)
        else SecureStorageResult.Failure(SecureStorageFailure(SecureStorageFailureCode.NOT_AVAILABLE))
    }

    override suspend fun capability(): SecureStorageCapability = blobStore.capability()

    override suspend fun commitActivationBundle(bundle: SecureActivationBundle): SecureStorageResult<SecureMaterialSnapshot> = mutex.withLock {
        val scope = scopeOf(bundle.metadata)
        val pointerName = pointerName(scope)
        val previousPointer = readPointer(pointerName, scope)
        val newGenerationIdValue = newGenerationId()

        val revision = SecureMaterialRevision((previousPointer?.revision ?: -1) + 1)
        val committedMetadata = bundle.metadata.copy(
            credentialRevision = revision.value, lastCommittedAtIso8601 = nowIso8601(),
        )

        val components = buildComponents(newGenerationIdValue, committedMetadata, bundle)
        for ((name, ad, bytes) in components) {
            val writeResult = blobStore.put(name, ad, bytes)
            if (writeResult is SecureStorageResult.Failure) {
                cleanupGeneration(newGenerationIdValue, components.map { it.name })
                return@withLock SecureStorageResult.Failure(SecureStorageFailure(SecureStorageFailureCode.WRITE_FAILED, "component write failed before promotion"))
            }
        }
        for ((name, ad, _) in components) {
            val readBack = blobStore.get(name, ad)
            if (readBack !is SecureStorageResult.Success || readBack.value == null) {
                cleanupGeneration(newGenerationIdValue, components.map { it.name })
                return@withLock SecureStorageResult.Failure(SecureStorageFailure(SecureStorageFailureCode.PARTIAL_COMMIT, "staged component failed verification read-back"))
            }
        }

        val newPointer = PointerRecord(generation = newGenerationIdValue, revision = revision.value)
        val pointerBytes = json.encodeToString(PointerRecord.serializer(), newPointer).encodeToByteArray()
        val promoteResult = blobStore.put(pointerName, pointerAssociatedData(scope), pointerBytes)
        if (promoteResult is SecureStorageResult.Failure) {
            cleanupGeneration(newGenerationIdValue, components.map { it.name })
            return@withLock SecureStorageResult.Failure(SecureStorageFailure(SecureStorageFailureCode.PARTIAL_COMMIT, "pointer promotion failed -- old generation remains current"))
        }

        if (previousPointer != null && previousPointer.generation != newGenerationIdValue) {
            cleanupGeneration(previousPointer.generation, generationComponentNames(previousPointer.generation, scope))
        }

        SecureStorageResult.Success(SecureMaterialSnapshot(scope, revision, committedMetadata.lastCommittedAtIso8601))
    }

    override suspend fun loadActivationBundle(scope: SecureMaterialScope): SecureStorageResult<SecureActivationBundle?> = mutex.withLock {
        val pointer = readPointer(pointerName(scope), scope) ?: return@withLock SecureStorageResult.Success(null)
        loadGeneration(pointer.generation, scope)
    }

    override suspend fun deleteScope(scope: SecureMaterialScope): SecureStorageResult<Unit> = mutex.withLock {
        val pointerNameValue = pointerName(scope)
        val pointer = readPointer(pointerNameValue, scope)
        if (pointer != null) cleanupGeneration(pointer.generation, generationComponentNames(pointer.generation, scope))
        blobStore.delete(pointerNameValue)
    }

    override suspend fun rotateKey(): SecureStorageResult<Unit> = blobStore.rotateKey()

    override suspend fun recoverInterruptedCommit(scope: SecureMaterialScope): SecureStorageResult<SecureStorageRecoveryOutcome> = mutex.withLock {
        val pointer = readPointer(pointerName(scope), scope) ?: return@withLock SecureStorageResult.Success(SecureStorageRecoveryOutcome.NoRecoveryNeeded)
        val loadResult = loadGeneration(pointer.generation, scope)
        when (loadResult) {
            is SecureStorageResult.Success -> if (loadResult.value != null) {
                SecureStorageResult.Success(SecureStorageRecoveryOutcome.RecoveredToLastGoodGeneration(SecureMaterialSnapshot(scope, SecureMaterialRevision(pointer.revision), loadResult.value.metadata.lastCommittedAtIso8601)))
            } else {
                SecureStorageResult.Success(SecureStorageRecoveryOutcome.UnrecoverableReactivationRequired)
            }
            is SecureStorageResult.Failure -> SecureStorageResult.Success(SecureStorageRecoveryOutcome.UnrecoverableReactivationRequired)
        }
    }

    override suspend fun health(scope: SecureMaterialScope): SecureStorageResult<SecureStorageHealth> {
        val capability = blobStore.capability()
        val pointer = readPointer(pointerName(scope), scope)
        val bundleResult = if (pointer != null) loadGeneration(pointer.generation, scope) else SecureStorageResult.Success(null)
        val recoveryRequired = pointer != null && (bundleResult !is SecureStorageResult.Success || bundleResult.value == null)
        return SecureStorageResult.Success(
            SecureStorageHealth(
                adapterAvailable = capability.available,
                keyAvailable = capability.available,
                currentStorageVersion = SecureMaterialVersion.CURRENT.storageFormatVersion,
                activationBundleExists = pointer != null,
                migrationRequired = false,
                recoveryRequired = recoveryRequired,
                keyInvalidated = false,
                lastSuccessfulCommitAtIso8601 = (bundleResult as? SecureStorageResult.Success)?.value?.metadata?.lastCommittedAtIso8601,
                safeErrorCode = if (recoveryRequired) SecureStorageFailureCode.CORRUPT_DATA else null,
            ),
        )
    }

    // ---- Real, private, generation/pointer machinery ----

    @Serializable
    private data class PointerRecord(@SerialName("generation") val generation: String, @SerialName("revision") val revision: Int)

    private fun scopeOf(metadata: SecureActivationBundleMetadata) = SecureMaterialScope(
        productCode = metadata.productCode, installationId = metadata.ownerInstallationId, accountId = metadata.customerAccountId,
    )

    private fun pointerName(scope: SecureMaterialScope) = "pointer:${scope.productCode}:${scope.installationId ?: "-"}:${scope.accountId ?: "-"}"
    private fun pointerAssociatedData(scope: SecureMaterialScope) = "pointer|${scope.productCode}|${scope.installationId}".encodeToByteArray()

    private suspend fun readPointer(name: String, scope: SecureMaterialScope): PointerRecord? {
        // Associated data must match exactly what commitActivationBundle wrote via pointerAssociatedData(scope).
        val result = blobStore.get(name, pointerAssociatedData(scope))
        return (result as? SecureStorageResult.Success)?.value?.let {
            try { json.decodeFromString(PointerRecord.serializer(), it.decodeToString()) } catch (e: Exception) { null }
        }
    }

    private data class Component(val name: String, val associatedData: ByteArray, val bytes: ByteArray)

    private fun buildComponents(generationId: String, metadata: SecureActivationBundleMetadata, bundle: SecureActivationBundle): List<Component> {
        val scope = scopeOf(metadata)
        fun blob(materialType: SecureMaterialType, bytes: ByteArray): Component {
            val key = SecureMaterialKey(materialType, scope)
            val name = "gen:$generationId:${materialType.name}"
            return Component(name, secureAssociatedData(key, SecureMaterialVersion.CURRENT), bytes)
        }
        val list = mutableListOf<Component>()
        list += blob(SecureMaterialType.ACTIVATION_BUNDLE_METADATA, json.encodeToString(SecureActivationBundleMetadata.serializer(), metadata).encodeToByteArray())
        bundle.customerAccessCredential?.let { list += blob(SecureMaterialType.CUSTOMER_ACCESS_CREDENTIAL, it.expose().encodeToByteArray()) }
        bundle.customerRefreshCredential?.let { list += blob(SecureMaterialType.CUSTOMER_REFRESH_CREDENTIAL, it.expose().encodeToByteArray()) }
        list += blob(SecureMaterialType.INSTALLATION_IDENTITY_SEED, encodeIdentity(bundle.installationIdentity))
        list += blob(SecureMaterialType.INSTALLATION_CREDENTIAL, bundle.installationCredential.expose().encodeToByteArray())
        list += blob(SecureMaterialType.SIGNED_LEASE, json.encodeToString(SignedAssertionEnvelope.serializer(), bundle.rawSignedLease).encodeToByteArray())
        return list
    }

    private fun generationComponentNames(generationId: String, scope: SecureMaterialScope): List<String> =
        SecureMaterialType.entries.map { "gen:$generationId:${it.name}" }

    private suspend fun cleanupGeneration(generationId: String, names: List<String>) {
        for (name in names) blobStore.delete(name)
    }

    private suspend fun loadGeneration(generationId: String, scope: SecureMaterialScope): SecureStorageResult<SecureActivationBundle?> {
        suspend fun read(materialType: SecureMaterialType): ByteArray? {
            val key = SecureMaterialKey(materialType, scope)
            val name = "gen:$generationId:${materialType.name}"
            val result = blobStore.get(name, secureAssociatedData(key, SecureMaterialVersion.CURRENT))
            return (result as? SecureStorageResult.Success)?.value
        }

        val metadataBytes = read(SecureMaterialType.ACTIVATION_BUNDLE_METADATA) ?: return SecureStorageResult.Success(null)
        val metadata = try { json.decodeFromString(SecureActivationBundleMetadata.serializer(), metadataBytes.decodeToString()) } catch (e: Exception) {
            return SecureStorageResult.Failure(SecureStorageFailure(SecureStorageFailureCode.CORRUPT_DATA, "metadata undecodable"))
        }

        val installationSeedBytes = read(SecureMaterialType.INSTALLATION_IDENTITY_SEED) ?: return SecureStorageResult.Failure(SecureStorageFailure(SecureStorageFailureCode.CORRUPT_DATA, "missing installation identity component"))
        val installationCredentialBytes = read(SecureMaterialType.INSTALLATION_CREDENTIAL) ?: return SecureStorageResult.Failure(SecureStorageFailure(SecureStorageFailureCode.CORRUPT_DATA, "missing installation credential component"))
        val leaseBytes = read(SecureMaterialType.SIGNED_LEASE) ?: return SecureStorageResult.Failure(SecureStorageFailure(SecureStorageFailureCode.CORRUPT_DATA, "missing signed lease component"))

        val identity = try { decodeIdentity(installationSeedBytes) } catch (e: Exception) {
            return SecureStorageResult.Failure(SecureStorageFailure(SecureStorageFailureCode.CORRUPT_DATA, "installation identity undecodable"))
        }
        val lease = try { json.decodeFromString(SignedAssertionEnvelope.serializer(), leaseBytes.decodeToString()) } catch (e: Exception) {
            return SecureStorageResult.Failure(SecureStorageFailure(SecureStorageFailureCode.CORRUPT_DATA, "signed lease undecodable"))
        }

        val accessCredential = read(SecureMaterialType.CUSTOMER_ACCESS_CREDENTIAL)?.let { ExternalCustomerAccessCredential(it.decodeToString()) }
        val refreshCredential = read(SecureMaterialType.CUSTOMER_REFRESH_CREDENTIAL)?.let { ExternalCustomerRefreshCredential(it.decodeToString()) }

        return SecureStorageResult.Success(
            SecureActivationBundle(
                metadata = metadata,
                customerAccessCredential = accessCredential,
                customerRefreshCredential = refreshCredential,
                installationIdentity = identity,
                installationCredential = InstallationCredentialMaterial(installationCredentialBytes.decodeToString()),
                rawSignedLease = lease,
            ),
        )
    }

    @Serializable
    private data class IdentityDto(val value: String, val version: String, val status: String, val generatedAt: String)

    private fun encodeIdentity(identity: InstallationIdentity): ByteArray = json.encodeToString(
        IdentityDto.serializer(),
        IdentityDto(identity.seed.value, identity.seed.version.name, identity.status.name, identity.generatedAt),
    ).encodeToByteArray()

    private fun decodeIdentity(bytes: ByteArray): InstallationIdentity {
        val dto = json.decodeFromString(IdentityDto.serializer(), bytes.decodeToString())
        return InstallationIdentity(
            seed = LocalInstallationSeed(dto.value, InstallationIdentityVersion.valueOf(dto.version)),
            status = InstallationIdentityStatus.valueOf(dto.status),
            generatedAt = dto.generatedAt,
        )
    }
}
