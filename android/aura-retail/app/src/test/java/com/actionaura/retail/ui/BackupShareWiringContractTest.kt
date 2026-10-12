package com.actionaura.retail.ui

import com.google.common.truth.Truth.assertThat
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File

/**
 * BACKUP-1 reachability guard: a phone backup that cannot leave the phone dies
 * with the phone (the backup folder is the app sandbox). The feature is four
 * pieces that only work TOGETHER -- the download route in AuraApi, the Save /
 * Share buttons, the FileProvider that makes Share legal, and the `segno`
 * wheel -- and there is no Compose runner or device here, so, like the other
 * *WiringContractTest files, "is each piece actually connected" is a
 * source-reading question. Each assertion is on code (comments stripped), so
 * prose that merely NAMES a piece cannot keep a deleted call green.
 */
class BackupShareWiringContractTest {

    private val moduleRoot = File(".")

    private fun source(relative: String): String {
        val file = File(moduleRoot, relative)
        assumeTrue("$relative not reachable from this run context", file.exists())
        return file.readText()
    }

    @Test
    fun the_api_streams_the_backup_file_from_the_download_route() {
        val src = codeOnly(source("src/main/java/com/actionaura/retail/net/AuraApi.kt"))
        assertThat(src).contains("@Streaming")
        assertThat(src).contains("@GET(\"api/backup/download/{name}\")")
        assertThat(src).contains("suspend fun downloadBackup(")
    }

    @Test
    fun the_screen_saves_through_the_system_picker_and_shares_through_the_file_provider() {
        val src = codeOnly(source("src/main/java/com/actionaura/retail/ui/screens/BackupRestoreScreen.kt"))
        assertThat(src).contains("ActivityResultContracts.CreateDocument(\"application/zip\")")
        assertThat(src).contains("saveLauncher.launch(")
        assertThat(src).contains("FileProvider.getUriForFile(")
        assertThat(src).contains("Intent.ACTION_SEND")
        assertThat(src).contains("FLAG_GRANT_READ_URI_PERMISSION")
    }

    @Test
    fun the_file_provider_is_declared_unexported_and_only_exposes_the_share_folder() {
        val manifest = source("src/main/AndroidManifest.xml")
        assertThat(manifest).contains("androidx.core.content.FileProvider")
        assertThat(manifest).contains("\${applicationId}.backupshare")
        assertThat(manifest).contains("android:exported=\"false\"")
        val paths = source("src/main/res/xml/backup_share_paths.xml")
        assertThat(paths).contains("<cache-path name=\"backup_share\" path=\"backup_share/\"")
        // Nothing wider: a root/external path here would hand out the whole sandbox.
        assertThat(paths).doesNotContain("<root-path")
        assertThat(paths).doesNotContain("<files-path")
    }

    @Test
    fun segno_is_in_the_chaquopy_pip_list_at_the_same_version_as_the_desktop_requirements() {
        val gradle = source("build.gradle")
        val req = File(moduleRoot, "../../../requirements/base.txt")
        assumeTrue("requirements/base.txt not reachable from this run context", req.exists())
        val pinned = Regex("""(?m)^segno==([0-9.]+)""").find(req.readText())?.groupValues?.get(1)
        assertThat(pinned).isNotNull()
        // A literal install line, not a comment that mentions segno.
        assertThat(codeOnly(gradle)).contains("install \"segno==$pinned\"")
    }
}
