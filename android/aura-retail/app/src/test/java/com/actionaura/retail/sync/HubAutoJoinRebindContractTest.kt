package com.actionaura.retail.sync

import com.actionaura.retail.ui.codeOnly
import com.google.common.truth.Truth.assertThat
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File

/**
 * Reachability + ordering guard for [HubAutoJoinService]'s half of the hub
 * re-point fix (see `SyncCoordinatorRebindTest`'s own class doc for the
 * measured gap this closes). Two failure shapes this file exists to catch,
 * neither of which [HubAutoJoinServiceTest]'s existing pure-function tests
 * could ever see, because both are about a CALL SITE, not a return value:
 *
 *  1. [HubAutoJoinService.attemptJoin]'s `persistHub` callback could persist
 *     the new hub into [HubPrefs] without ever telling [SyncCoordinator] --
 *     the exact defect this task fixes. A test asserting only "HubPrefs.set
 *     was called" (as `HubPairingWiringContractTest`'s pre-existing sibling
 *     already does for the manual pairing screen) would stay green even if
 *     the re-point call were silently absent.
 *  2. [HubAutoJoinService.runOnce] could call [HubAutoJoinService.attemptJoin]
 *     without ever consulting [SyncCoordinator.readyToJoinHub] -- which would
 *     let a phone join a hub before it finished draining its own pre-hub
 *     history from the cloud (0.5/G3 in this task's plan).
 *
 * Same shape and justification as [HubAutoJoinWiringContractTest] and
 * `HubPairingWiringContractTest`: no Compose test runner, no Chaquopy and no
 * device in this environment, so "does the code actually call X before Y"
 * is a source-reading question, answered via the shared [codeOnly] helper.
 */
class HubAutoJoinRebindContractTest {

    private val moduleRoot = File(".")

    private fun source(relative: String): String {
        val file = File(moduleRoot, relative)
        assumeTrue("$relative not reachable from this run context", file.exists())
        return codeOnly(file.readText())
    }

    private val hubAutoJoinServiceSource: String get() =
        source("src/main/java/com/actionaura/retail/sync/HubAutoJoinService.kt")

    // -- (1) persistHub: HubPrefs.set() then the re-point, not the other way --

    @Test
    fun `persistHub calls HubPrefs set before re-pointing SyncCoordinator`() {
        // Bounded to the persistHub lambda's own body -- "when (result) {"
        // is the next CODE construct after it closes, so this cannot be
        // satisfied by some unrelated HubPrefs.set()/onHubPairingChanged()
        // pair living elsewhere in this file.
        val body = hubAutoJoinServiceSource
            .substringAfter("persistHub = {")
            .substringBefore("when (result) {")

        val setIndex = body.indexOf("HubPrefs.set(")
        val repointIndex = body.indexOf("SyncCoordinator.onHubPairingChanged(")
        assertThat(setIndex).isGreaterThan(-1)
        assertThat(repointIndex).isGreaterThan(-1)
        // Ordering, not just presence -- see HubPairingWiringContractTest's
        // identical "not before" test for the manual-pairing half of this
        // same discipline: HubPrefs holds the new hub's URL/pin, and
        // SyncCoordinator.onHubPairingChanged() re-reads HubPrefs itself
        // (see its own doc comment), so calling it BEFORE the write would
        // re-point against whatever was paired before, not the new hub.
        assertThat(repointIndex).isGreaterThan(setIndex)
    }

    // -- (2) runOnce: the catch-up gate is actually consulted, not bypassed --

    @Test
    fun `runOnce consults SyncCoordinator readyToJoinHub before attemptJoin`() {
        val body = hubAutoJoinServiceSource
            .substringAfter("private suspend fun runOnce(")
            .substringBefore("private suspend fun readLicenseIdentity(")

        val readyIndex = body.indexOf("SyncCoordinator.readyToJoinHub(")
        val attemptIndex = body.indexOf("attemptJoin(")
        assertThat(readyIndex).isGreaterThan(-1)
        assertThat(attemptIndex).isGreaterThan(-1)
        assertThat(readyIndex).isLessThan(attemptIndex)

        // Verifier-added, 2026-09-24: CONSULTED is not the same as APPLIED.
        // The three assertions above stay green if runOnce reads the gate
        // into a variable and then calls the 2-argument shouldAttemptJoin
        // (whose readyToJoin defaults to true) -- measured: that mutation
        // survived every test in this package. The value read from
        // readyToJoinHub() must be the argument that actually gates.
        val readyVar = Regex("""val\s+(\w+)\s*=\s*SyncCoordinator\.readyToJoinHub\(""")
            .find(body)?.groupValues?.get(1)
        assertThat(readyVar).isNotNull()
        assertThat(
            Regex("""shouldAttemptJoin\(\s*activated\s*,\s*alreadyJoined\s*,\s*$readyVar\s*\)""")
                .containsMatchIn(body)
        ).isTrue()
    }

    // -- (3) shouldAttemptJoin: the ready gate actually gates --------------

    @Test
    fun `shouldAttemptJoin -- not ready blocks an otherwise-eligible join`() {
        // MUTATION PROOF (report both directions verbatim per this task's
        // rules): with shouldAttemptJoin's real body (`activated &&
        // !alreadyJoined && readyToJoin`), false/true here is RED->GREEN as
        // intended. Dropping `&& readyToJoin` from the body turns the first
        // assertion RED, because shouldAttemptJoin(true, false, false)
        // would then return true regardless of readiness.
        assertThat(HubAutoJoinService.shouldAttemptJoin(activated = true, alreadyJoined = false, readyToJoin = false))
            .isFalse()
        assertThat(HubAutoJoinService.shouldAttemptJoin(activated = true, alreadyJoined = false, readyToJoin = true))
            .isTrue()
    }

    // -- (4) the existing 2-argument call sites/tests are unaffected -------

    @Test
    fun `existing two-argument calls keep their pre-existing behavior via the new parameter's default`() {
        assertThat(HubAutoJoinService.shouldAttemptJoin(activated = false, alreadyJoined = false)).isFalse()
        assertThat(HubAutoJoinService.shouldAttemptJoin(activated = true, alreadyJoined = true)).isFalse()
        assertThat(HubAutoJoinService.shouldAttemptJoin(activated = true, alreadyJoined = false)).isTrue()
    }
}
