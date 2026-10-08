package com.actionaura.retail.updates

import android.content.Context
import android.content.pm.PackageInfo
import android.content.pm.PackageManager
import android.os.Build
import java.io.File
import java.security.MessageDigest

/**
 * What the verifier needs to know about an APK on disk and about the app that
 * is already installed -- behind an interface so every refusal below is a plain
 * JVM test with no PackageManager, and so the production class can be swapped
 * for a fake that LIES in exactly the ways a hostile or broken archive would.
 */
interface ArchiveInspector {
    /** Null when the file is not a parseable APK at all. */
    fun inspectArchive(file: File): ArchiveInfo?

    /** SHA-256 (hex) of each certificate the INSTALLED app is signed with. */
    fun installedCertSha256s(): Set<String>
}

data class ArchiveInfo(
    val packageName: String,
    val versionCode: Long,
    /** SHA-256 (hex) of each certificate the archive is signed with; empty if unreadable. */
    val certSha256s: Set<String>,
)

/**
 * The gate between "bytes Owner served" and "bytes handed to Android's
 * installer". The download hash already proves the file is the one Owner
 * described; this proves it is also something THIS app may legitimately
 * become. Android would refuse most of these on its own (a different signer
 * cannot update us), but a refusal at the system dialog reads to a shopkeeper
 * as "the update is broken", and the system is the last line of defence here,
 * not the only one.
 *
 * Every method returns the failure code or null for "fine"; none throws.
 */
class ApkVerifier(
    private val inspector: ArchiveInspector,
    private val expectedPackage: String,
    private val installedVersionCode: Long,
) {

    /** Package, signer and version checks on the archive, in that order. */
    fun verifyArchive(file: File, offer: UpdateOffer): String? {
        val info = inspector.inspectArchive(file) ?: return UpdateErrorCode.DOWNLOAD_CORRUPT

        // Same package id or nothing: the debug build (.debug suffix) must only
        // ever update the debug build, and a release build must never be
        // replaced by some other app Owner (or someone in the middle) names.
        if (info.packageName != expectedPackage) return UpdateErrorCode.WRONG_PACKAGE

        // Same signer or nothing. Fails CLOSED on an empty set: an archive whose
        // certificates could not be read is not "unsigned and therefore fine".
        val installed = inspector.installedCertSha256s()
        if (info.certSha256s.isEmpty() || installed.isEmpty() || info.certSha256s != installed) {
            return UpdateErrorCode.SIGNATURE_MISMATCH
        }

        if (info.versionCode <= installedVersionCode) return UpdateErrorCode.NOT_NEWER

        // Python already refuses a version that is not newer by NAME; this is the
        // same question asked of the real archive. If Owner published a build
        // number it must be the one inside the file, or the descriptor described
        // some other build than the one we hold.
        if (offer.buildNumber != null && offer.buildNumber.toLong() != info.versionCode) {
            return UpdateErrorCode.DESCRIPTOR_INVALID
        }
        return null
    }

    /**
     * Re-hashes the file just before install. It sat in the cache directory
     * between the download and the user pressing "Update now", possibly for a
     * long while, and nothing but this stops a changed file being installed.
     */
    fun verifyUnchanged(file: File, offer: UpdateOffer): String? {
        if (!file.isFile) return UpdateErrorCode.INSTALLER_TAMPERED
        val actual = sha256Hex(file)
        if (!actual.equals(offer.checksumSha256, ignoreCase = true)) return UpdateErrorCode.INSTALLER_TAMPERED
        return null
    }
}

fun sha256Hex(file: File): String {
    val digest = MessageDigest.getInstance("SHA-256")
    file.inputStream().use { input ->
        val buffer = ByteArray(64 * 1024)
        while (true) {
            val n = input.read(buffer)
            if (n < 0) break
            digest.update(buffer, 0, n)
        }
    }
    return digest.digest().joinToString("") { "%02x".format(it) }
}

fun sha256Hex(bytes: ByteArray): String =
    MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }

/** The real thing, backed by PackageManager. */
class PackageManagerArchiveInspector(context: Context) : ArchiveInspector {
    private val appContext = context.applicationContext
    private val pm: PackageManager = appContext.packageManager

    @Suppress("DEPRECATION")
    override fun inspectArchive(file: File): ArchiveInfo? {
        val info: PackageInfo = try {
            pm.getPackageArchiveInfo(file.path, signatureFlag())
        } catch (_: Exception) {
            null
        } ?: return null
        return ArchiveInfo(info.packageName ?: return null, versionCodeOf(info), certsOf(info))
    }

    @Suppress("DEPRECATION")
    override fun installedCertSha256s(): Set<String> = try {
        certsOf(pm.getPackageInfo(appContext.packageName, signatureFlag()))
    } catch (_: Exception) {
        emptySet()
    }

    @Suppress("DEPRECATION")
    private fun signatureFlag(): Int =
        // API 28 introduced signingInfo (which understands v2+ signing). Below
        // that only the legacy v1 `signatures` exist, and getPackageArchiveInfo
        // can return none for an archive signed v2-only -- which reaches the
        // verifier as an empty set and is refused (fail closed) rather than
        // waved through. minSdk is 26, so this branch is real, not theoretical.
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) PackageManager.GET_SIGNING_CERTIFICATES
        else PackageManager.GET_SIGNATURES

    private fun versionCodeOf(info: PackageInfo): Long =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) info.longVersionCode
        else @Suppress("DEPRECATION") info.versionCode.toLong()

    private fun certsOf(info: PackageInfo): Set<String> {
        @Suppress("DEPRECATION")
        val signatures = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            // The CURRENT signers (apkContentsSigners), for the archive and the
            // installed app alike, so the two sets are comparable like for like.
            info.signingInfo?.apkContentsSigners
        } else {
            info.signatures
        }
        return signatures?.map { sha256Hex(it.toByteArray()) }?.toSet() ?: emptySet()
    }
}
