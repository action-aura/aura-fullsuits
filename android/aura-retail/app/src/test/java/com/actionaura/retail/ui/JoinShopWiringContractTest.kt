package com.actionaura.retail.ui

import com.google.common.truth.Truth.assertThat
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File

/**
 * Reachability + contract guards for the join-existing-shop feature
 * (FirstRunDecision, ui/screens/JoinShopScreens.kt). Same shape and
 * justification as EmployeesWiringContractTest and ColorTokenContractTest:
 * there is no Compose test runner, no Chaquopy and no device in this
 * environment, so "does AppRoot actually route through FirstRunDecision" and
 * "does the joining screen actually avoid minting a second account" are
 * source-reading contract questions.
 */
class JoinShopWiringContractTest {

    private val moduleRoot = File(".")

    private fun source(relative: String): String {
        val file = File(moduleRoot, relative)
        assumeTrue("$relative not reachable from this run context", file.exists())
        return file.readText()
    }

    // Spelled as a literal at the call, not through a shared constant: see
    // ColorTokenContractTest's identical note -- CompiledTestSuiteRollCallTest's
    // completeness scan only credits a guard with "opens a path I can see"
    // when the path is a string literal at a helper call.
    private val appRootKt: String get() = source("src/main/java/com/actionaura/retail/ui/AppRoot.kt")
    private val joinShopScreensKt: String get() =
        source("src/main/java/com/actionaura/retail/ui/screens/JoinShopScreens.kt")

    @Test
    fun app_root_routes_the_first_run_decision_through_the_shared_object() {
        val src = codeOnly(appRootKt)
        assertThat(src).contains("FirstRunDecision.decide(")
        assertThat(src).contains("Phase.JOIN_CHOICE ->")
        assertThat(src).contains("Phase.JOINING ->")
    }

    @Test
    fun app_root_no_longer_decides_setup_inline() {
        // The bare pre-FirstRunDecision shape: a needs_setup check that jumps
        // straight to Phase.SETUP without ever consulting FirstRunDecision
        // would silently reintroduce the bug this feature fixes (a joining
        // device forced through account creation).
        assertThat(codeOnly(appRootKt)).doesNotContain("if (needsSetup) return Phase.SETUP")
    }

    @Test
    fun joining_screen_polls_onboarding_status_and_never_creates_an_admin() {
        val src = codeOnly(joinShopScreensKt)
        assertThat(src).contains("onboardingStatus()")
        // The whole point of this screen: a joining device inherits its
        // owner's account from sync and must never manufacture a placeholder
        // one of its own (see the file's doc comment and the desktop's
        // `_joinSubmit()`, which carries the identical rule).
        assertThat(src).doesNotContain("createAdmin")
    }

    @Test
    fun setup_instead_is_offered_both_before_and_after_the_timeout() {
        // A mis-tap on the choice screen must be one tap to undo without
        // waiting the full ceiling out, so the escape hatch has to appear
        // twice: the always-visible TextButton and the post-timeout button.
        val key = "\"Set up a new shop instead\""
        val count = Regex(Regex.escape(key)).findAll(codeOnly(joinShopScreensKt)).count()
        assertThat(count).isAtLeast(2)
    }
}
