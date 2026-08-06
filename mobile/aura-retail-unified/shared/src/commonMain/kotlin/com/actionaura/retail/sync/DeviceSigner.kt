package com.actionaura.retail.sync

import com.actionaura.retail.securestorage.SecureBlobStore

/**
 * Task 7 (multi-device-sync-foundation) -- the real, net-new client-side
 * signing capability this codebase previously had zero of: verification of
 * server-issued signed leases already existed
 * (`SignedLeaseSignatureVerifier`), but no device could produce its own
 * Ed25519 signature. `DeviceSigner` is the one authority every later sync
 * capability signs through -- Task 8 (activation, publishing the device's
 * public key to Owner) and Task 9 (the sync client, signing push/pull
 * requests) both consume this, never re-implement key generation/signing.
 *
 * A device has exactly one signing keypair for its whole install lifetime:
 * generated once on first use, then persisted so `publicKeyBytes()` returns
 * the SAME bytes across every later app launch -- Owner registers whatever
 * public key the device first presents at activation (Task 8), so a
 * regenerated key would silently and permanently break Owner-side
 * verification of everything this device signs afterward.
 */
interface DeviceSigner {
    /** The device's persisted Ed25519 public key, raw 32 bytes -- same bytes on every call, across every process launch. */
    suspend fun publicKeyBytes(): ByteArray

    /** Real Ed25519 signature (raw 64 bytes) over [message], produced with the device's persisted private key. Never exposes the private key itself. */
    suspend fun sign(message: ByteArray): ByteArray
}

/**
 * Real per-platform signing implementation. Takes the same narrow
 * [SecureBlobStore] primitive `AuraAppContainer` already receives from each
 * platform entry point (`AndroidSecureBlobStore`/`IosSecureBlobStore`) --
 * see the Android `actual`'s own KDoc for why this wraps [SecureBlobStore]
 * directly rather than `GenerationalSecureMaterialStore`.
 */
expect class PlatformDeviceSigner(secureBlobStore: SecureBlobStore) : DeviceSigner
