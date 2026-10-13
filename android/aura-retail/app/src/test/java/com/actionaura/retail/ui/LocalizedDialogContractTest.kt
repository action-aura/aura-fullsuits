package com.actionaura.retail.ui

import com.google.common.truth.Truth.assertThat
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File

/**
 * ANDROID-3 item 1: in Arabic the dialog TITLE sat on the left and the button
 * row kept its left-to-right order. ANDROID-2a had re-provided the direction
 * inside each slot of Material's AlertDialog, which fixed the content but not
 * the two things AlertDialog places itself -- those are outside every slot.
 *
 * There is no Compose runner here (the emulator run in the ANDROID-3 note is
 * the pixel proof), so this reads the one file that owns every dialog in the
 * app and pins the three properties that make the fix work:
 *   1. it is NOT built on Material's AlertDialog (whose title/button placement
 *      is what ignored the app's direction),
 *   2. the direction is provided around the WHOLE surface, not per slot,
 *   3. the buttons are in a Row whose arrangement is End and whose first child
 *      is the dismiss button (so Arabic mirrors it: confirm on the left).
 * Each is mutation-proved (the mutation log is in the ANDROID-3 progress notes).
 */
class LocalizedDialogContractTest {

    private val src: String get() {
        val f = File("src/main/java/com/actionaura/retail/ui/screens/LocalizedAlertDialog.kt")
        assumeTrue("LocalizedAlertDialog.kt not reachable from this run context", f.exists())
        return codeOnly(f.readText())
    }

    @Test
    fun it_is_not_built_on_materials_alert_dialog() {
        // the function is itself called LocalizedAlertDialog( - only a bare Material call is forbidden
        assertThat(Regex("""(?<!Localized)AlertDialog\(""").containsMatchIn(src)).isFalse()
        assertThat(src).contains("Dialog(")
    }

    @Test
    fun the_app_direction_wraps_the_whole_surface_not_each_slot() {
        val provide = src.indexOf("CompositionLocalProvider(LocalLayoutDirection provides dir)")
        val surface = src.indexOf("Surface(")
        val title = src.indexOf("title()")
        val confirm = src.indexOf("confirmButton()")
        assertThat(provide).isGreaterThan(-1)
        assertThat(surface).isGreaterThan(provide)
        assertThat(title).isGreaterThan(surface)
        assertThat(confirm).isGreaterThan(title)
        // exactly one provider of the layout direction: a per-slot one would be the old, insufficient fix
        assertThat(Regex("""LocalLayoutDirection provides""").findAll(src).count()).isEqualTo(1)
    }

    @Test
    fun the_button_row_puts_dismiss_first_and_packs_to_the_end_edge() {
        assertThat(src).contains("horizontalArrangement = Arrangement.End")
        assertThat(src.indexOf("dismissButton?.let")).isLessThan(src.indexOf("confirmButton()"))
    }

    @Test
    fun the_title_starts_at_the_start_edge() {
        assertThat(src).contains("contentAlignment = Alignment.CenterStart")
    }
}
