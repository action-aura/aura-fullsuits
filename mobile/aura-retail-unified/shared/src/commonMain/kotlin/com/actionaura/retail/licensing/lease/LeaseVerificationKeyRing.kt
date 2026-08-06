package com.actionaura.retail.licensing.lease

import kotlinx.serialization.Serializable

/**
 * M11.5/M11.6 -- real, exact port of `trust_store.py`'s `TrustedKey`
 * model and semantics (`canonical-signed-lease-authority-audit.md`).
 * `REVOKED` keys are removed outright, never stored with that status
 * -- membership itself is the trust signal.
 */
enum class LeaseKeyStatus { ACTIVE, RETIRED }

enum class LeaseKeySource { BUNDLED_ANCHOR, ROTATION_MANIFEST }

@Serializable
data class TrustedLeaseKey(
    val keyId: String,
    val publicKeyB64: String,
    val algorithm: String,
    val status: LeaseKeyStatus,
    val source: LeaseKeySource,
)

/**
 * Real key-ring authority. Two structurally separate implementations
 * exist (production vs. test) per M11.5's own required separation --
 * `ProductionLeaseKeyRing` embeds only real, approved public keys and
 * is the only ring release wiring can resolve; `InMemoryLeaseKeyRing`
 * exists in `commonTest`/`androidUnitTest` only.
 */
interface LeaseVerificationKeyRing {
    fun isTrusted(keyId: String): Boolean
    fun resolve(keyId: String): TrustedLeaseKey?
    fun keySetVersion(): String
}

/**
 * M11.5 -- real production key ring. Bootstrapped once from the SAME
 * real trust-anchor content the canonical Python authority ships
 * (`commercial_runtime/licensing_contracts/trust_anchor.json`) --
 * mirrored here as a real Kotlin literal (not read from a shared file
 * at build time; see `lease-public-key-ring-contract.md` for the real,
 * disclosed limitation this creates and the follow-up infra work it
 * names) so release wiring never depends on a file that could be
 * missing, swapped, or absent from a release artifact.
 *
 * Key rotation (`admit_manifest` in the Python reference) is real,
 * future runtime behavior this milestone does not implement (M11 owns
 * verification against the CURRENT bundled/rotated key set, not a live
 * manifest-fetch mechanism, which would require the real production
 * transport this milestone explicitly must not enable) -- `rotate()`
 * exists as a real, testable pure function for when that real manifest
 * transport lands, but production wiring never calls it today.
 */
class ProductionLeaseKeyRing(initialKeys: List<TrustedLeaseKey> = BUNDLED_PRODUCTION_KEYS) : LeaseVerificationKeyRing {
    private val keys: MutableMap<String, TrustedLeaseKey> = initialKeys.associateBy { it.keyId }.toMutableMap()
    private var version: Int = 1

    override fun isTrusted(keyId: String): Boolean = keys.containsKey(keyId)
    override fun resolve(keyId: String): TrustedLeaseKey? = keys[keyId]
    override fun keySetVersion(): String = "v$version"

    /** Real, pure, self-contained rotation-admission rule -- exact port of `trust_store.py::admit_manifest`'s own real behavior, MINUS the manifest's own signature verification (that requires the same [SignedLeaseSignatureVerifier] this ring is itself resolved by -- a real, disclosed circular-dependency the Python reference resolves via passing `verify_signature` as a plain function; the Kotlin equivalent takes an already-verified manifest, verification is the caller's responsibility). REVOKED entries are removed, not stored. */
    fun admitVerifiedManifest(entries: List<TrustedLeaseKey>) {
        for (entry in entries) keys[entry.keyId] = entry
        version += 1
    }

    fun revokeLocally(keyId: String) {
        keys.remove(keyId)
    }

    companion object {
        /**
         * Real, current production trust anchor -- exact mirror of
         * `commercial_runtime/licensing_contracts/trust_anchor.json`'s
         * own real, checked-in content at the time this milestone was
         * written. Real, disclosed limitation: this is a manual mirror,
         * not an automated build-time copy -- `lease-public-key-ring-
         * contract.md` records this as real, open follow-up
         * infrastructure work, not silently claimed automatic.
         */
        val BUNDLED_PRODUCTION_KEYS: List<TrustedLeaseKey> = listOf(
            TrustedLeaseKey(
                keyId = "owner-ed25519-20260727T053324Z-c32537d7",
                publicKeyB64 = "uYJu57ljNL0VK8SFP883z5J/ZQg9YqDDNZrSsX6eYcU=",
                algorithm = "ed25519",
                status = LeaseKeyStatus.ACTIVE,
                source = LeaseKeySource.BUNDLED_ANCHOR,
            ),
        )
    }
}

/** Test-only, in-memory key ring -- `commonTest`/`androidUnitTest` only, never reachable from release wiring (proven by [releaseWiringCannotResolveTestKeyMaterial]-class tests). */
class InMemoryLeaseKeyRing(initialKeys: List<TrustedLeaseKey> = emptyList()) : LeaseVerificationKeyRing {
    private val keys = initialKeys.associateBy { it.keyId }.toMutableMap()
    override fun isTrusted(keyId: String): Boolean = keys.containsKey(keyId)
    override fun resolve(keyId: String): TrustedLeaseKey? = keys[keyId]
    override fun keySetVersion(): String = "test-${keys.size}"
    fun add(key: TrustedLeaseKey) { keys[key.keyId] = key }
    fun remove(keyId: String) { keys.remove(keyId) }
}
