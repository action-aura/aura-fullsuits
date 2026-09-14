package com.actionaura.retail.ui

import com.google.common.truth.Truth.assertThat
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File

/**
 * The Employees screen must keep re-reading the staff list while it is open.
 *
 * Same shape and justification as BranchPinWiringContractTest and its
 * siblings: no Compose test runner, no Chaquopy and no device here, so
 * scanning the source is the only seam for "does this screen still do the
 * thing it was fixed to do".
 *
 * ── THE DEFECT THIS PINS, measured on real hardware 2026-09-14 ──────────────
 *
 * Every row on this screen can be changed by a DIFFERENT DEVICE. An owner
 * invites a cashier on the desktop till; the cashier sets their password; the
 * row moves out of `pending_setup` into `active`. None of that touches the
 * phone.
 *
 * The screen loaded exactly once, in `LaunchedEffect(Unit)`, and never again.
 * So a phone left on the Employees screen showed "Invite pending" for a
 * cashier who had finished setting up minutes earlier -- while that same
 * device's own registry.db already read `status=active`, and the cashier could
 * already sign in on that very phone. Nothing on screen admitted the data was
 * stale, which is the expensive part: the badge was not wrong about the data
 * it had, it was showing data that had stopped being true.
 *
 * This is exactly the class of regression a multi-device product invites and
 * a single-device one never sees, so it is worth a guard rather than a memory.
 *
 * ── WHY THESE ASSERTIONS AND NOT A SCREENSHOT ───────────────────────────────
 *
 * Three separate properties, because each can regress on its own:
 *   1. there is a repeating refresh at all;
 *   2. it is silent -- a background tick must never flip `loading` back on,
 *      or the skeleton flashes over the list every ten seconds;
 *   3. a failed background tick must not replace a good list with an error.
 *
 * Property 3 is the subtle one. Reusing `load()` for the poll would look
 * correct and would mean one dropped request wipes the owner's list and shows
 * an error banner nobody asked for, until the next tick happens to succeed.
 */
class EmployeesRefreshContractTest {

    private val moduleRoot = File(".")

    private fun source(relative: String): String {
        val file = File(moduleRoot, relative)
        assumeTrue("$relative not reachable from this run context", file.exists())
        return file.readText()
    }

    private fun employeesScreenCode(): String =
        codeOnly(source("src/main/java/com/actionaura/retail/ui/screens/EmployeesScreen.kt"))

    @Test
    fun `the employees screen refreshes on a repeating timer, not once`() {
        val code = employeesScreenCode()

        assertThat(code).contains("EMPLOYEE_REFRESH_MILLIS")
        // A loop plus a delay is what makes it repeat. Asserting on the
        // LaunchedEffect alone would still pass for the load-once version that
        // caused the defect.
        assertThat(code).contains("while (true)")
        assertThat(code).contains("delay(EMPLOYEE_REFRESH_MILLIS)")
    }

    /** The body of `refreshQuietly`, which is the only code that runs
     *  unattended on a timer and therefore the only code these rules bind. */
    private fun refreshQuietlyBody(): String {
        val code = employeesScreenCode()
        val start = code.indexOf("suspend fun refreshQuietly")
        assertThat(start).isGreaterThan(-1)
        val end = code.indexOf("LaunchedEffect(Unit)", start)
        assertThat(end).isGreaterThan(start)
        return code.substring(start, end)
    }

    @Test
    fun `the repeating refresh is silent and never re-shows the skeleton`() {
        // Asserted against the BACKGROUND path only, not a count over the
        // whole file. `loading = true` legitimately appears twice elsewhere --
        // the first load and the "Try again" button on the error state -- and
        // both are attended, so both SHOULD show the skeleton. An earlier
        // draft of this test counted occurrences file-wide and failed on that
        // perfectly correct retry button, which would have pushed someone
        // toward deleting a good affordance to satisfy a test.
        assertThat(refreshQuietlyBody()).doesNotContain("loading")
    }

    @Test
    fun `a failed background refresh keeps the rows and says they may be stale`() {
        val body = refreshQuietlyBody()

        // The rows must survive: a dropped request is not a reason to destroy
        // a list the owner is reading.
        assertThat(body).doesNotContain("employees = emptyList()")
        // ...but the failure must still be surfaced, through the same shared
        // mapping every other failure on this screen uses. Silently keeping
        // stale rows is a milder version of the very bug this refresh fixes.
        assertThat(body).contains("refreshError = apiErrorMessage")
    }

    @Test
    fun `staleness is reported separately from a failed first load`() {
        val code = employeesScreenCode()

        // Two distinct states, deliberately. `loadError` replaces the list
        // ("I have never read this"); `refreshError` sits beside it ("I read
        // it, but not just now"). Collapsing them would mean one dropped
        // background request wipes the screen.
        assertThat(code).contains("var refreshError")
        assertThat(code).contains("refreshError?.let")
    }
}
