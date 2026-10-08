package com.actionaura.retail.updates

import com.google.common.truth.Truth.assertThat
import org.junit.Test
import org.w3c.dom.Element
import java.io.File
import javax.xml.parsers.DocumentBuilderFactory

/**
 * Source-content guards for the in-app updater wiring that has no unit-testable
 * seam (a manifest entry, "is the daily loop started", "is the card admin-only").
 * Same shape and justification as LicensingWiringContractTest: no device and no
 * Compose runner here, but these are exactly the regressions nobody sees until a
 * shop's till does or does not update.
 */
class UpdatesWiringContractTest {

    private val moduleRoot = File(".")

    private fun source(relative: String): String {
        val file = File(moduleRoot, relative)
        assertThat(file.exists()).isTrue()
        return file.readText()
    }

    private fun manifest() = DocumentBuilderFactory.newInstance().newDocumentBuilder()
        .parse(File(moduleRoot, "src/main/AndroidManifest.xml"))

    private fun elements(tag: String): List<Element> {
        val nodes = manifest().getElementsByTagName(tag)
        return (0 until nodes.length).map { nodes.item(it) as Element }
    }

    @Test
    fun the_install_permission_is_declared() {
        // Without it the PackageInstaller session is refused outright.
        val names = elements("uses-permission").map { it.getAttribute("android:name") }
        assertThat(names).contains("android.permission.REQUEST_INSTALL_PACKAGES")
    }

    @Test
    fun the_install_result_receiver_is_declared_and_not_exported() {
        // exported=true would let any app on the phone post a fake "install
        // succeeded" or hand the receiver a confirm screen of its choosing.
        val receivers = elements("receiver")
        val ours = receivers.filter { it.getAttribute("android:name") == ".updates.InstallResultReceiver" }
        assertThat(ours).hasSize(1)
        assertThat(ours.single().getAttribute("android:exported")).isEqualTo("false")
        // And nothing else in the manifest quietly became an exported receiver.
        val exported = receivers.filter { it.getAttribute("android:exported") != "false" }
        assertThat(exported).isEmpty()
    }

    @Test
    fun the_receiver_the_manifest_names_really_exists_and_the_installer_targets_it() {
        // A renamed class would leave a manifest entry pointing at nothing and an
        // install whose result nobody hears.
        Class.forName("com.actionaura.retail.updates.InstallResultReceiver", false, javaClass.classLoader)
        assertThat(source("src/main/java/com/actionaura/retail/updates/ApkInstaller.kt"))
            .contains("Intent(context, InstallResultReceiver::class.java)")
    }

    @Test
    fun no_file_provider_is_declared_because_the_installer_uses_a_session() {
        // A FileProvider would be a door into the private cache just to hand a
        // file to a viewer; the session API writes the bytes straight in.
        assertThat(elements("provider")).isEmpty()
    }

    @Test
    fun the_daily_check_is_actually_started_at_boot() {
        assertThat(source("src/main/java/com/actionaura/retail/ui/AppRoot.kt"))
            .contains("UpdateCoordinator.start(")
    }

    @Test
    fun the_background_cadence_is_two_minutes_then_daily() {
        assertThat(UpdateCoordinator.INITIAL_DELAY_MS).isEqualTo(2L * 60 * 1000)
        assertThat(UpdateCoordinator.INTERVAL_MS).isEqualTo(24L * 60 * 60 * 1000)
    }

    @Test
    fun the_background_loop_only_checks_it_never_downloads_or_installs() {
        val coordinator = source("src/main/java/com/actionaura/retail/updates/UpdateCoordinator.kt")
        val loop = coordinator.substringAfter("job = scope.launch {").substringBefore("fun stop()")
        assertThat(loop).contains("updateFlow.check()")
        assertThat(loop).doesNotContain(".download(")
        assertThat(loop).doesNotContain(".install(")
    }

    @Test
    fun the_updates_card_sits_inside_the_admin_only_backup_block_and_hides_without_an_owner_url() {
        val screens = source("src/main/java/com/actionaura/retail/ui/screens/RetailExtraScreens.kt")
        val backupHeader = screens.indexOf("SectionHeader(tr(\"Backup & restore\"))")
        assertThat(backupHeader).isGreaterThan(-1)
        // The admin guard is the line right above the Backup header...
        assertThat(screens.substring(maxOf(0, backupHeader - 120), backupHeader))
            .contains("RetailSession.isAdmin")
        // ...and the card is called before that guard's block ends (the next section).
        val untilLicensing = screens.substring(backupHeader).substringBefore("SectionHeader(tr(\"Licensing\"))")
        assertThat(untilLicensing).contains("UpdateCoordinator.isConfigured")
        assertThat(untilLicensing).contains("UpdatesSection()")
    }

    @Test
    fun every_error_code_has_its_own_plain_sentence() {
        val ui = source("src/main/java/com/actionaura/retail/ui/screens/UpdatesSection.kt")
        // ALL itself must be complete: derive the real set from the class.
        val declared = UpdateErrorCode::class.java.declaredFields
            .filter { java.lang.reflect.Modifier.isStatic(it.modifiers) && it.type == String::class.java }
            .map { it.get(null) as String }
            .toSet()
        assertThat(UpdateErrorCode.ALL.toSet()).isEqualTo(declared)
        assertThat(UpdateErrorCode.ALL).containsNoDuplicates()

        val missing = declared.filterNot { ui.contains("UpdateErrorCode.$it ->") }
        assertThat(missing).isEmpty()
    }
}
