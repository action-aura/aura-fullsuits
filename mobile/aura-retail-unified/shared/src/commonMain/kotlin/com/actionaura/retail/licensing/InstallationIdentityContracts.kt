package com.actionaura.retail.licensing

/**
 * M8.4 -- privacy-safe Installation identity contract shape
 * (`installation-identity-seed-contract.md`). No real random-byte
 * generation or platform secure storage here -- that is real
 * ANDROID_ADAPTER/IOS_ADAPTER work, a later milestone
 * (`licensing-gap-ownership-matrix.md` gaps #8/#9).
 */
enum class InstallationIdentityVersion { V1 }

enum class InstallationIdentityStatus { GENERATED, PERSISTED_SECURELY, RECOVERY_REQUIRED, LOST }

data class LocalInstallationSeed(
    val value: String,
    val version: InstallationIdentityVersion,
) {
    /** Never leak the raw seed through logs/crash reports via toString(). */
    override fun toString(): String = "LocalInstallationSeed(version=$version, value=<redacted>)"
}

data class InstallationIdentity(
    val seed: LocalInstallationSeed,
    val status: InstallationIdentityStatus,
    val generatedAt: String,
) {
    override fun toString(): String = "InstallationIdentity(status=$status, generatedAt=$generatedAt, seed=<redacted>)"
}
