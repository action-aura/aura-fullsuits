package com.actionaura.retail.ui

import com.google.common.truth.Truth.assertThat
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File

/**
 * Reachability guard for automatic LAN hub discovery.
 *
 * THIS TEST EXISTS BECAUSE THE FEATURE SHIPPED DEAD AND EVERY UNIT TEST WAS
 * GREEN. `HubAutoJoinService`, `HubDiscovery`, `HubTransport` and
 * `SpkiPinning` were all built, reviewed and covered -- 447 tests, 0 skipped
 * -- and NOTHING ANYWHERE CALLED `HubAutoJoinService.start()`, so not a
 * single tick ever ran on a real device. It was found on hardware, not in
 * the suite: a phone on the shop wifi, holding the same licence as a live
 * hub whose beacon it could hear, was left running for 100 seconds and
 * emitted ZERO log lines from the tag. Zero lines is the signature of
 * "never started", as opposed to the several lines a tick that ran and
 * declined would have produced.
 *
 * No amount of testing `HubAutoJoinService`'s own logic could have caught
 * that, which is the point: the defect was in the ONE LINE that was never
 * written, and a guard on a call site is the only test shape that sees a
 * missing call. Same shape and justification as
 * `JoinShopWiringContractTest` and `EmployeesWiringContractTest` -- there is
 * no Compose test runner, no Chaquopy and no device here, so "does AppRoot
 * actually start this loop" is a source-reading question.
 */
class HubAutoJoinWiringContractTest {

    private val moduleRoot = File(".")

    private fun source(relative: String): String {
        val file = File(moduleRoot, relative)
        assumeTrue("$relative not reachable from this run context", file.exists())
        return file.readText()
    }

    // Spelled as a literal at the call, not through a shared constant --
    // see ColorTokenContractTest's identical note.
    private val appRootKt: String get() = source("src/main/java/com/actionaura/retail/ui/AppRoot.kt")

    @Test
    fun app_root_actually_starts_the_hub_auto_join_loop() {
        // codeOnly() strips comments deliberately: AppRoot's own comment at
        // this call site NAMES HubAutoJoinService.start() while explaining
        // why the line matters, so a prose-only match would keep this test
        // green even if the real call were deleted -- which is exactly the
        // regression it exists to catch.
        val src = codeOnly(appRootKt)
        assertThat(src).contains("HubAutoJoinService.start(")
    }

    @Test
    fun the_loop_starts_only_after_the_embedded_server_is_up() {
        // Ordering is a correctness constraint, not tidiness: the first
        // thing a tick does is call the embedded Python backend's
        // /_internal/license-identity to learn which shop this device
        // belongs to. Started before ServerBootstrap.start(ctx), every early
        // tick would fail that read, fall back to blanks, and decline to
        // join -- recoverable on a later tick, but silently wrong at exactly
        // the moment a freshly-launched till is most likely to be waiting
        // for its hub.
        val src = codeOnly(appRootKt)
        val serverStart = src.indexOf("ServerBootstrap.start(")
        val autoJoinStart = src.indexOf("HubAutoJoinService.start(")
        assertThat(serverStart).isGreaterThan(-1)
        assertThat(autoJoinStart).isGreaterThan(-1)
        assertThat(autoJoinStart).isGreaterThan(serverStart)
    }
}
