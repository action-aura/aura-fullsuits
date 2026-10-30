package com.actionaura.retail.licensing

import com.actionaura.retail.ui.i18n.arabicFor
import com.google.common.truth.Truth.assertThat
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File

/**
 * The phone's side of the 7-day trial lock. The lock itself is decided in Python (TRIAL_ENDED,
 * policy_evaluator.py) and enforced by the capability guard on every sale; Android only DISPLAYS it,
 * so these pin (1) that the display block is read faithfully, (2) that the lock screen's copy has
 * Arabic, and (3) that the state label table actually knows TRIAL_ENDED -- the shape of bug where a
 * new Python state falls through `else -> state` and prints a raw code to a customer.
 */
class TrialStatusTest {

    private fun status(vararg trial: Pair<String, Any?>): Map<String, Any?> =
        mapOf("current_state" to "ACTIVE_ONLINE", "trial" to mapOf(*trial))

    @Test
    fun a_running_trial_reports_its_days() {
        val t = TrialStatus.from(status("is_trial" to true, "ended" to false, "days_left" to 3))!!
        assertThat(t.isRunning).isTrue()
        assertThat(t.daysLeft).isEqualTo(3)
    }

    @Test
    fun days_left_may_arrive_as_a_double_from_the_json_parser() {
        val t = TrialStatus.from(status("is_trial" to true, "ended" to false, "days_left" to 2.0))!!
        assertThat(t.daysLeft).isEqualTo(2)
    }

    @Test
    fun a_paid_lease_an_ended_trial_and_zero_days_are_not_running() {
        assertThat(TrialStatus.from(status("is_trial" to false, "ended" to false, "days_left" to null))!!.isRunning).isFalse()
        assertThat(TrialStatus.from(status("is_trial" to true, "ended" to true, "days_left" to null))!!.isRunning).isFalse()
        assertThat(TrialStatus.from(status("is_trial" to true, "ended" to false, "days_left" to 0))!!.isRunning).isFalse()
    }

    @Test
    fun a_status_without_a_trial_block_is_null_not_a_crash() {
        assertThat(TrialStatus.from(mapOf("current_state" to "ACTIVE_ONLINE"))).isNull()
        assertThat(TrialStatus.from(mapOf("current_state" to "ACTIVE_ONLINE", "trial" to "garbage"))).isNull()
    }

    @Test
    fun every_lock_screen_string_has_an_arabic_translation() {
        val keys = listOf(
            "Trial ended", "Free trial", "Trial: 1 day left", "Trial: 2 days left", "Trial: %s days left",
            "Your free trial has ended",
            "Your data is safe. You can still look at your records and reports, and make a backup or an export. Selling is locked until you pay.",
            "Back up and share my data", "How to unlock",
            "Contact your Aura seller, or pay by reference number when available.",
            "After paying, connect to the internet and the app unlocks automatically.",
        )
        val missing = keys.filter { arabicFor(it).isNullOrBlank() || arabicFor(it) == it }
        assertThat(missing).isEmpty()
    }

    @Test
    fun the_state_label_table_knows_trial_ended_and_the_python_state_exists() {
        val suiteRoot = File("../../..")
        val py = File(suiteRoot, "commercial_runtime/licensing_contracts/state_machine.py")
        assumeTrue("state_machine.py not reachable from the test working directory", py.isFile)
        assertThat(py.readText()).contains("TRIAL_ENDED = \"TRIAL_ENDED\"")
        val screen = File("src/main/java/com/actionaura/retail/ui/screens/LicensingScreen.kt")
        assumeTrue("LicensingScreen.kt not reachable from the test working directory", screen.isFile)
        assertThat(screen.readText()).contains("\"TRIAL_ENDED\" -> \"Trial ended\"")
    }
}
