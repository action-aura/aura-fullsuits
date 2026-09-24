package com.actionaura.retail.ui

import com.google.common.truth.Truth.assertThat
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File

/**
 * The manual-pairing half of the hub re-point fix (see
 * `com.actionaura.retail.sync.SyncCoordinatorRebindTest`'s own class doc for
 * the measured gap). [HubPairingWiringContractTest] already proves the
 * "Shop network" section is reachable and pins the pairing call's own
 * security shape (pinned transport, ordering against the 200-check, no
 * logged pairing code, translation coverage); this sibling file is scoped
 * narrowly to the ONE thing that file predates: that BOTH the connect and
 * the disconnect paths actually tell [com.actionaura.retail.sync
 * .SyncCoordinator] their pairing state just changed, not merely
 * [com.actionaura.retail.sync.HubPrefs].
 *
 * A test asserting only "HubPrefs.set/clear was called" (as the existing
 * sibling file's own tests already do) would stay green even if the
 * re-point call were silently absent -- exactly the shape of defect this
 * task's own measurement found on real hardware (phone paired, HubPrefs
 * correct, SyncCoordinator still signing against the old relay: the hub
 * recorded 0 site_sync_nonces from the phone until the app was restarted,
 * and 37 after).
 */
class HubPairingRebindContractTest {

    private val moduleRoot = File(".")

    private fun source(relative: String): String {
        val file = File(moduleRoot, relative)
        assumeTrue("$relative not reachable from this run context", file.exists())
        return file.readText()
    }

    private val extraScreens get() = source("src/main/java/com/actionaura/retail/ui/screens/RetailExtraScreens.kt")

    /** Identical bound to [HubPairingWiringContractTest]'s own
     *  `retailSettingsBody` -- comments stripped, bounded to the next
     *  unrelated screen so an assertion about THIS control cannot be
     *  satisfied by code belonging to `PurchaseOrdersScreen` or anything
     *  else in this large file. */
    private val retailSettingsBody: String get() =
        codeOnly(extraScreens)
            .substringAfter("fun RetailSettingsScreen(")
            .substringBefore("fun PurchaseOrdersScreen(")

    // -- Connect: HubPrefs.set(...) then the re-point ----------------------

    @Test
    fun `a successful connect re-points SyncCoordinator after persisting the new hub`() {
        // Bounded to the Connected branch's own body -- the next sibling
        // branch is the next CODE construct after it closes, so this
        // cannot be satisfied by the Disconnect button's own
        // HubPrefs.clear()/re-point pair below it in the same screen.
        val body = retailSettingsBody
            .substringAfter("is HubConnectOutcome.Connected -> {")
            .substringBefore("is HubConnectOutcome.Failed ->")

        val setIndex = body.indexOf("HubPrefs.set(")
        val repointIndex = body.indexOf("SyncCoordinator.onHubPairingChanged(")
        assertThat(setIndex).isGreaterThan(-1)
        assertThat(repointIndex).isGreaterThan(-1)
        // Ordering, not just presence: SyncCoordinator.onHubPairingChanged()
        // re-reads HubPrefs itself (see its own doc comment), so calling it
        // BEFORE HubPrefs.set() would re-point against whatever hub (or
        // nothing) was paired before this connect, not the new one.
        assertThat(repointIndex).isGreaterThan(setIndex)
    }

    // -- Disconnect: HubPrefs.clear(...) then the re-point -----------------

    @Test
    fun `disconnecting re-points SyncCoordinator after clearing the hub pairing`() {
        val clearIndex = retailSettingsBody.indexOf("HubPrefs.clear(ctx)")
        assertThat(clearIndex).isGreaterThan(-1)

        // Bounded to a window immediately AFTER the clear call -- proves
        // ordering by construction (the search starts at the clear call's
        // own index), and stays well clear of the Connect button's own
        // HubPrefs.set()/re-point pair elsewhere in this same screen.
        val window = retailSettingsBody.substring(
            clearIndex,
            minOf(retailSettingsBody.length, clearIndex + 300),
        )
        assertThat(window).contains("SyncCoordinator.onHubPairingChanged(")
    }
}
