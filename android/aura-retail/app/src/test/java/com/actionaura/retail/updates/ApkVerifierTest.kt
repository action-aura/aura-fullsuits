package com.actionaura.retail.updates

import com.google.common.truth.Truth.assertThat
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File

/**
 * Every way an archive can be wrong, one test each, against the pure
 * [ApkVerifier] with an inspector that reports exactly what the test says.
 * The allow-half (a good archive passes) is pinned too: a verifier that
 * refused everything would pass every refusal test and ship no update ever.
 */
class ApkVerifierTest {

    @get:Rule
    val tempFolder = TemporaryFolder()

    private val pkg = "com.actionaura.retail"
    private val installedCerts = setOf(sha256Hex("installed-cert".toByteArray()))

    private fun offer(buildNumber: Int? = 10, checksum: String = "00") = UpdateOffer(
        productVersionId = "pv-1", version = "1.0.0-rc.9", buildNumber = buildNumber,
        checksumSha256 = checksum, sizeBytes = 4, releaseNotes = null,
        updateRequired = false, belowMinSupported = false,
    )

    private fun verifier(archive: ArchiveInfo?, installed: Set<String> = installedCerts, installedVersionCode: Long = 9) =
        ApkVerifier(
            object : ArchiveInspector {
                override fun inspectArchive(file: File) = archive
                override fun installedCertSha256s() = installed
            },
            expectedPackage = pkg,
            installedVersionCode = installedVersionCode,
        )

    private fun good() = ArchiveInfo(pkg, 10, installedCerts)
    private fun file() = tempFolder.newFile().also { it.writeText("apk!") }

    @Test
    fun `a good archive passes`() {
        assertThat(verifier(good()).verifyArchive(file(), offer())).isNull()
    }

    @Test
    fun `a good archive passes when Owner published no build number`() {
        assertThat(verifier(good()).verifyArchive(file(), offer(buildNumber = null))).isNull()
    }

    @Test
    fun `a different package is refused`() {
        // The .debug build must only ever update the .debug build.
        val wrong = good().copy(packageName = "com.actionaura.retail.debug")
        assertThat(verifier(wrong).verifyArchive(file(), offer())).isEqualTo(UpdateErrorCode.WRONG_PACKAGE)
    }

    @Test
    fun `a different signer is refused`() {
        val other = good().copy(certSha256s = setOf(sha256Hex("someone-else".toByteArray())))
        assertThat(verifier(other).verifyArchive(file(), offer())).isEqualTo(UpdateErrorCode.SIGNATURE_MISMATCH)
    }

    @Test
    fun `an archive with an extra signer is refused, signer sets must be equal`() {
        val extra = good().copy(certSha256s = installedCerts + sha256Hex("extra".toByteArray()))
        assertThat(verifier(extra).verifyArchive(file(), offer())).isEqualTo(UpdateErrorCode.SIGNATURE_MISMATCH)
    }

    @Test
    fun `unreadable certificates fail closed on either side`() {
        assertThat(verifier(good().copy(certSha256s = emptySet())).verifyArchive(file(), offer()))
            .isEqualTo(UpdateErrorCode.SIGNATURE_MISMATCH)
        assertThat(verifier(good(), installed = emptySet()).verifyArchive(file(), offer()))
            .isEqualTo(UpdateErrorCode.SIGNATURE_MISMATCH)
    }

    @Test
    fun `the same or an older version code is refused`() {
        assertThat(verifier(good().copy(versionCode = 9)).verifyArchive(file(), offer(buildNumber = null)))
            .isEqualTo(UpdateErrorCode.NOT_NEWER)
        assertThat(verifier(good().copy(versionCode = 3)).verifyArchive(file(), offer(buildNumber = null)))
            .isEqualTo(UpdateErrorCode.NOT_NEWER)
    }

    @Test
    fun `a build number that disagrees with the archive is refused`() {
        assertThat(verifier(good().copy(versionCode = 11)).verifyArchive(file(), offer(buildNumber = 10)))
            .isEqualTo(UpdateErrorCode.DESCRIPTOR_INVALID)
    }

    @Test
    fun `a file that is not an archive at all is refused`() {
        assertThat(verifier(null).verifyArchive(file(), offer())).isEqualTo(UpdateErrorCode.DOWNLOAD_CORRUPT)
    }

    @Test
    fun `an unchanged file passes the pre-install re-hash and a changed one does not`() {
        val f = file()
        val o = offer(checksum = sha256Hex(f))
        val v = verifier(good())
        assertThat(v.verifyUnchanged(f, o)).isNull()
        // Same length, one byte different: the size alone would not notice.
        f.writeText("apk?")
        assertThat(v.verifyUnchanged(f, o)).isEqualTo(UpdateErrorCode.INSTALLER_TAMPERED)
    }

    @Test
    fun `a missing file fails the pre-install re-hash`() {
        val f = file()
        val o = offer(checksum = sha256Hex(f))
        f.delete()
        assertThat(verifier(good()).verifyUnchanged(f, o)).isEqualTo(UpdateErrorCode.INSTALLER_TAMPERED)
    }

    @Test
    fun `the checksum compare ignores hex case`() {
        val f = file()
        assertThat(verifier(good()).verifyUnchanged(f, offer(checksum = sha256Hex(f).uppercase()))).isNull()
    }
}
