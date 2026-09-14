package com.actionaura.retail.ui

import com.google.common.truth.Truth.assertThat
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File

/**
 * Reachability + safety guards for the "Shop network" settings section
 * (retail-hardware-viewports) -- the control that lets a phone pair to
 * another till acting as the shop's LAN hub by redeeming the pairing text
 * that hub's own "Connect a device" screen shows (`site-relay.js`/`.html`,
 * `commercial_runtime/sync/site_relay/pairing.py` + `routes.py`'s `/pair`).
 *
 * Same shape and same justification as BranchPinWiringContractTest and its
 * siblings: no Compose test runner, no Chaquopy and no device in this
 * environment, but "is this control actually on the screen a user reaches"
 * and "does the security-critical wiring actually hold together" are
 * precisely the regressions that stay invisible until an owner -- or an
 * attacker on the shop's own wifi -- finds them first.
 */
class HubPairingWiringContractTest {

    private val moduleRoot = File(".")

    private fun source(relative: String): String {
        val file = File(moduleRoot, relative)
        assumeTrue("$relative not reachable from this run context", file.exists())
        return file.readText()
    }

    private val appRoot get() = source("src/main/java/com/actionaura/retail/ui/AppRoot.kt")
    private val extraScreens get() = source("src/main/java/com/actionaura/retail/ui/screens/RetailExtraScreens.kt")

    /**
     * RetailSettingsScreen's own body, comments stripped, bounded to the
     * next unrelated screen so an assertion about THIS control cannot be
     * satisfied by code that belongs to PurchaseOrdersScreen or anything
     * else in this large file. Mirrors BranchPinWiringContractTest's
     * `retailSettingsBody` exactly.
     */
    private val retailSettingsBody: String get() =
        codeOnly(extraScreens)
            .substringAfter("fun RetailSettingsScreen(")
            .substringBefore("fun PurchaseOrdersScreen(")

    // ── (1) Reachability: the control is on the screen users actually reach ──

    @Test
    fun the_retail_settings_route_is_registered_in_the_nav_graph() {
        val graph = appRoot.substringAfter("private fun retailGraph(")
        assertThat(graph).contains("""b.composable("retail_settings")""")
        assertThat(graph).contains("RetailSettingsScreen(")
    }

    @Test
    fun the_shop_network_section_is_part_of_retail_settings_screen() {
        // Bounded to RetailSettingsScreen's own body (see retailSettingsBody)
        // with comments stripped, so this only goes green for the real
        // control wired to the real pairing call -- not for a doc comment
        // that merely talks about one, and not for a copy sitting in some
        // other screen in this file.
        val body = retailSettingsBody
        assertThat(body).contains("""tr("Shop network")""")
        assertThat(body).contains("attemptHubConnect(")
        assertThat(body).contains("HubPrefs.set(")
    }

    // ── (2) The pairing call uses the pinned transport ────────────────────────

    /**
     * WHY AN UNPINNED PAIRING POST WOULD BE WORSE THAN USELESS: `/pair` is
     * reachable to anything that can route to the hub's LAN port (see
     * routes.py's own docstring on that route -- TLS's ordinary hostname/
     * chain checks do not protect it, only the pairing code does, and only
     * for WHICH device gets admitted). If this device's POST were not
     * pinned to the hub's known key, it would happily complete a TLS
     * handshake with WHATEVER answered on that address -- an attacker who
     * spoofed the hub's IP, or a device that raced to grab it after a DHCP
     * lease changed. That impostor would receive, in the clear (from its
     * own point of view -- it terminated the TLS itself), this device's
     * public key AND the live, single-use pairing code the operator is
     * actively holding open right now. It could then either redeem the
     * code itself against the real hub before this device does, or simply
     * answer "200 OK" and let this device store the IMPOSTOR'S address in
     * HubPrefs as "the shop's hub" -- after which every future sync
     * attempt signs and sends this device's real sales data to it. Pinning
     * the connection to the hub's SPKI key (learned out-of-band, from the
     * very text just pasted) is what makes "answered on the right IP" and
     * "is actually the hub" the same fact.
     */
    @Test
    fun the_pairing_call_uses_the_pinned_transport() {
        val body = retailSettingsBody
        assertThat(body).contains(""""/api/sync/v1/pair"""")
        assertThat(body).contains("SpkiPinning")
    }

    // ── (3) HubPrefs.set only happens after a successful response ────────────

    @Test
    fun hub_prefs_set_is_called_after_the_response_check_not_before() {
        // Ordering, not just presence: a mutation that hoisted HubPrefs.set
        // above the 200-check would store an unpaired (or rejected) hub as
        // though it had been accepted -- see PART's own "CONSUMED BEFORE
        // ANYTHING IS WRITTEN" reasoning in routes.py for the server-side
        // half of the same discipline. Checked by raw text position because
        // there is no Compose/coroutine runner here to execute the branch.
        val body = retailSettingsBody
        val responseCheckIndex = body.indexOf("response.code == 200")
        val setIndex = body.indexOf("HubPrefs.set(")
        assertThat(responseCheckIndex).isGreaterThan(-1)
        assertThat(setIndex).isGreaterThan(-1)
        assertThat(setIndex).isGreaterThan(responseCheckIndex)
    }

    // ── (4) A way to unpair exists ────────────────────────────────────────────

    @Test
    fun the_screen_offers_a_way_to_unpair() {
        assertThat(retailSettingsBody).contains("HubPrefs.clear(")
    }

    // ── (5) The pairing code is never logged ──────────────────────────────────

    @Test
    fun the_pairing_code_is_never_passed_to_a_logging_call() {
        // pairing.py's own module doc: "No code is ever written to a log
        // line or included in a PairingError message anywhere in this
        // module" -- this is that same property, pasted forward to the
        // Android side of the same credential. Scans every Log.*/println
        // call in the bounded screen body for any reference to the
        // pasted text or the code parsed out of it.
        val body = retailSettingsBody
        val loggingCalls = Regex("""(?:Log\.[deiwv]|println)\([^)]*\)""")
        val offenders = loggingCalls.findAll(body)
            .map { it.value }
            .filter { it.contains("pairingCode") || it.contains("hubPairingText") || it.contains("payload.pairingCode") }
            .toList()
        assertThat(offenders).isEmpty()
    }

    // ── (6) Every tr() key this section introduces is in the catalogue ───────

    @Test
    fun every_translated_string_the_shop_network_section_uses_is_in_the_catalogue() {
        val catalog = literalRuns(codeOnly(
            source("src/main/java/com/actionaura/retail/ui/i18n/Strings.kt"))).toSet()
        val used = trKeys(retailSettingsBody).toSet()
        assertThat(used).isNotEmpty()
        assertThat(used.filterNot { it in catalog }.sorted()).isEmpty()
    }
}
