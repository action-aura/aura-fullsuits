package com.actionaura.retail.ui

import com.google.common.truth.Truth.assertThat
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File

/**
 * Every write button the Android app hides for a person who cannot use it is
 * hidden by a capability list COPIED from a server decorator. A copy drifts:
 * the server moves a route from `retail.stock.adjust` to something else and the
 * phone keeps offering (or hiding) the button. This reads the decorators out of
 * `retail_api.py` and compares them with the Kotlin lists, so the copy cannot
 * drift silently.
 *
 * Two halves, both needed:
 *  - PIN: each Kotlin list equals the decorator of the route(s) its button calls
 *    (behaviour on parsed Python, not a restated table).
 *  - WIRING: each gated screen actually reads its list, and the write control
 *    sits behind the flag. Source-text, so "contract-tested", not proved on a
 *    device -- there is no Compose runner here.
 *
 * Front-door advice only. The server re-gates every call regardless.
 */
class WriteGateContractTest {

    private val moduleRoot = File(".")
    private val suiteRoot = File("../../..")

    private fun kotlin(relative: String): String {
        val f = File(moduleRoot, relative)
        assumeTrue("$relative not reachable from this run context", f.exists())
        return codeOnly(f.readText())
    }

    private val retailApi: String get() {
        val f = File(suiteRoot, "products/retail/backend/api/retail_api.py")
        assumeTrue("retail_api.py not reachable from this run context", f.exists())
        return f.readText()
    }

    /** CAP_NAME -> 'retail.x' for every constant user_accounts.py defines. */
    private val serverCodes: Map<String, String> get() {
        val f = File(suiteRoot, "commercial_runtime/identity/user_accounts.py")
        assumeTrue("user_accounts.py not reachable from this run context", f.exists())
        return Regex("""(?m)^(CAP_\w+) = '([^']+)'""").findAll(f.readText())
            .associate { it.groupValues[1] to it.groupValues[2] }
    }

    /**
     * The capability codes the route function [fn] demands, read from its own
     * decorator stack: the text between the nearest `@retail_bp.route(` above the
     * `def` and the `def` itself, comments removed. `any` is true for
     * `mt_require_any_capability`.
     */
    private data class RouteGate(val codes: List<String>, val any: Boolean)

    private fun routeGate(fn: String): RouteGate {
        val py = retailApi
        val defAt = Regex("""(?m)^def $fn\(""").find(py)?.range?.first
        assertThat(defAt).isNotNull()
        val head = py.substring(0, defAt!!)
        val stack = head.substring(head.lastIndexOf("@retail_bp.route("))
            .lines().filterNot { it.trimStart().startsWith("#") }.joinToString("\n")
        val table = serverCodes
        val single = Regex("""(?m)^@mt_require_capability\((\w+)\)""").find(stack)
        val anyOf = Regex("""(?m)^@mt_require_any_capability\(([^)]*)\)""").find(stack)
        assertThat(listOf(single, anyOf).count { it != null }).isEqualTo(1)
        return if (single != null) {
            RouteGate(listOf(table.getValue(single.groupValues[1])), any = false)
        } else {
            RouteGate(
                anyOf!!.groupValues[1].split(",").map { it.trim() }.filter { it.isNotEmpty() }
                    .map { table.getValue(it) },
                any = true,
            )
        }
    }

    // ── PIN: Kotlin lists == server decorators ───────────────────────────────

    @Test
    fun the_parser_reads_real_decorators_and_tells_single_from_any_of() {
        // Guards the harness itself: if it returned empty or wrong-shaped gates
        // every comparison below would pass or fail for the wrong reason.
        assertThat(routeGate("create_return")).isEqualTo(RouteGate(listOf("retail.refund"), false))
        assertThat(routeGate("customer_payment").any).isTrue()
        assertThat(routeGate("customer_payment").codes)
            .containsExactly("retail.sell", "retail.books.record")
    }

    @Test
    fun returns_button_matches_create_return() {
        val g = routeGate("create_return")
        assertThat(g.any).isFalse()
        assertThat(CAPS_CREATE_RETURN).containsExactlyElementsIn(g.codes)
    }

    @Test
    fun stock_write_buttons_match_every_route_they_call() {
        // Categories x3, suppliers, purchase orders and receiving share ONE list;
        // if the server ever splits one of them off, this names which.
        for (fn in listOf(
            "create_category", "update_category", "delete_category",
            "create_supplier", "create_purchase_order", "receive_purchase_order",
            // ANDROID-1007: close-short and every stock-transfer write the phone
            // now offers sit behind the same list as Receive.
            "close_purchase_order_short",
            "create_stock_transfer", "send_stock_transfer", "receive_stock_transfer",
            "cancel_stock_transfer", "recall_stock_transfer",
        )) {
            val g = routeGate(fn)
            assertThat(g.any).isFalse()
            assertThat(CAPS_STOCK_WRITE).containsExactlyElementsIn(g.codes)
        }
    }

    @Test
    fun customer_payment_button_matches_its_any_of_decorator() {
        val g = routeGate("customer_payment")
        assertThat(g.any).isTrue()
        assertThat(CAPS_CUSTOMER_PAYMENT).containsExactlyElementsIn(g.codes)
    }

    @Test
    fun supplier_side_payment_buttons_match_books_record() {
        for (fn in listOf("supplier_payment", "pay_purchase_order")) {
            val g = routeGate(fn)
            assertThat(g.any).isFalse()
            assertThat(CAPS_SUPPLIER_PAYMENT).containsExactlyElementsIn(g.codes)
        }
    }

    @Test
    fun void_gates_are_the_decorator_floor_for_customers_and_books_record_for_suppliers() {
        val g = routeGate("void_payment")
        assertThat(g.any).isTrue()
        assertThat(CAPS_VOID_CUSTOMER_PAYMENT).containsExactlyElementsIn(g.codes)

        // The supplier half is narrowed IN THE BODY of void_payment, so the
        // decorator cannot pin it. Pin the narrowing itself: the body must ask
        // for books.record specifically, and our list must be exactly that.
        val body = retailApi.substringAfter("\ndef void_payment(").substringBefore("\n@retail_bp.route(")
        assertThat(body).contains("CAP_BOOKS_RECORD")
        assertThat(CAPS_VOID_SUPPLIER_PAYMENT).containsExactly(serverCodes.getValue("CAP_BOOKS_RECORD"))
        // ...and the narrowing must be strictly narrower than the floor, or the
        // two Kotlin lists would be the same list and the split pointless.
        assertThat(CAPS_VOID_CUSTOMER_PAYMENT).containsAtLeastElementsIn(CAPS_VOID_SUPPLIER_PAYMENT)
        assertThat(CAPS_VOID_CUSTOMER_PAYMENT).isNotEqualTo(CAPS_VOID_SUPPLIER_PAYMENT)
    }

    @Test
    fun the_three_statement_books_screens_are_gated_on_reports_exactly_as_the_server_is() {
        val appRoot = kotlin("src/main/java/com/actionaura/retail/ui/AppRoot.kt")
        val list = appRoot.substringAfter("private val booksEntries = listOf(").substringBefore(") @Composable")
        // CHANGED (rehearsal fix B7, cfbf883f): the server moved the three
        // statements from retail.reports to retail.books.view (managers hold
        // reports but must not read the books). The expectation is still read
        // from the parsed decorator, so it fails the day the gate moves again;
        // what it can no longer catch is a statement card left on CAP_REPORTS
        // being "right" -- that is now the bug.
        val serverReports = serverCodes.getValue("CAP_BOOKS_VIEW")
        for ((route, fn) in listOf(
            "trial_balance" to "report_trial_balance",
            "profit_and_loss" to "report_profit_and_loss",
            "balance_sheet" to "report_balance_sheet",
        )) {
            val g = routeGate(fn)
            assertThat(g.codes).containsExactly(serverReports)
            // The entry for this route must carry CAP_BOOKS_VIEW -- and only that.
            // codeOnly() collapses the list onto one line, so isolate THIS
            // entry: from its constructor up to the next one.
            val line = list.substringAfter("BooksEntry(\"$route\"").substringBefore("BooksEntry(")
            assertThat(line).contains(", CAP_BOOKS_VIEW)")
            assertThat(line).doesNotContain("CAP_REPORTS")
        }
        assertThat(CAP_BOOKS_VIEW).isEqualTo(serverReports)
    }

    // ── WIRING: the screens read the lists, and the control sits behind them ─

    @Test
    fun each_gated_screen_reads_the_list_that_matches_its_call() {
        val extra = kotlin("src/main/java/com/actionaura/retail/ui/screens/RetailExtraScreens.kt")
        for (line in listOf(
            "val canReturn = RetailSession.hasAnyCapability(CAPS_CREATE_RETURN)",
            "val canPay = RetailSession.hasAnyCapability(CAPS_CUSTOMER_PAYMENT)",
            "val canVoid = RetailSession.hasAnyCapability(CAPS_VOID_CUSTOMER_PAYMENT)",
            "val canAdd = RetailSession.hasAnyCapability(CAPS_STOCK_WRITE)",
            "val canPay = RetailSession.hasAnyCapability(CAPS_SUPPLIER_PAYMENT)",
            "val canVoid = RetailSession.hasAnyCapability(CAPS_VOID_SUPPLIER_PAYMENT)",
            "val canCreate = RetailSession.hasAnyCapability(CAPS_STOCK_WRITE)",
            "val canPayPo = RetailSession.hasAnyCapability(CAPS_SUPPLIER_PAYMENT)",
            "val canReceive = RetailSession.hasAnyCapability(CAPS_STOCK_WRITE)",
        )) {
            assertThat(extra).contains(line)
        }
        val cats = kotlin("src/main/java/com/actionaura/retail/ui/screens/CategoriesScreen.kt")
        assertThat(cats).contains("val canWrite = RetailSession.hasAnyCapability(CAPS_STOCK_WRITE)")
    }

    @Test
    fun the_write_controls_sit_behind_their_flag() {
        val extra = kotlin("src/main/java/com/actionaura/retail/ui/screens/RetailExtraScreens.kt")
        for (guarded in listOf(
            "if (rows.isNotEmpty() && canReturn) ExtendedFloatingActionButton(",
            "if (showNew && canReturn) ProcessReturnSheet(",
            "if (canVoid && e.kind == \"payment\" && e.payment_id != null) {",
            "if (canPay) {",
            "if (suppliers.isNotEmpty() && canAdd) ExtendedFloatingActionButton(",
            "if (showAdd && canAdd) AddSupplierSheet(",
            "if (orders.isNotEmpty() && canCreate) ExtendedFloatingActionButton(",
            "if (showCreate && canCreate) CreatePoSheet(",
            "if (canPayPo && (po?.payment_status ?: \"unpaid\") != \"paid\") {",
            // ANDROID-1007 CHANGED PIN: was `po?.status == "pending"` alone. A
            // partly received order must still offer Receive, so the gate now
            // also admits "partial". What this line can no longer catch on its
            // own: Receive being offered for "partial" by accident -- it is now
            // the intended behaviour; the "received"/"closed"/"cancelled"
            // exclusion is pinned separately in the test below.
            "if (canReceive && (po?.status == \"pending\" || po?.status == \"partial\")) {",
            "if (canReceive && po?.status == \"partial\") {",
        )) {
            assertThat(extra).contains(guarded)
        }
        // The transfer screen: its own file, its own flag, every write behind it.
        val transfers = kotlin("src/main/java/com/actionaura/retail/ui/screens/StockTransfersScreen.kt")
        assertThat(transfers).contains("val canWrite = RetailSession.hasAnyCapability(CAPS_STOCK_WRITE)")
        assertThat(transfers).contains("if (showCreate && canWrite) CreateTransferSheet(")
        assertThat(transfers).contains("if (transfers.isNotEmpty() && canWrite) ExtendedFloatingActionButton(")
        assertThat(transfers).contains("if (canWrite) {")
        val cats = kotlin("src/main/java/com/actionaura/retail/ui/screens/CategoriesScreen.kt")
        assertThat(cats).contains("if (categories.isNotEmpty() && canWrite) ExtendedFloatingActionButton(")
        assertThat(cats).contains("if (showAdd && canWrite) AddOrEditCategorySheet(")
        assertThat(cats).contains("CategoryRow(c, onClick = { if (canWrite) editing = c })")
    }

    @Test
    fun receive_is_never_offered_for_a_finished_purchase_order() {
        // ANDROID-1007: the other half of widening the gate to "partial" -- the
        // terminal statuses must stay out of it.
        val extra = kotlin("src/main/java/com/actionaura/retail/ui/screens/RetailExtraScreens.kt")
        val gate = extra.substringAfter("if (canReceive && (po?.status").substringBefore("{")
        for (terminal in listOf("received", "closed", "cancelled")) {
            assertThat(gate).doesNotContain("\"$terminal\"")
        }
    }

    @Test
    fun the_read_only_empty_state_lines_have_arabic_entries() {
        val strings = File(moduleRoot, "src/main/java/com/actionaura/retail/ui/i18n/Strings.kt")
        assumeTrue("Strings.kt not reachable from this run context", strings.exists())
        val catalog = Regex(""""((?:\\.|[^"\\])*)"\s+to""").findAll(strings.readText())
            .map { it.groupValues[1] }.toSet()
        assertThat(catalog).containsAtLeast(
            "Returns will show here.", "Suppliers will show here.",
            "Purchase orders will show here.", "Categories will show here.",
            "You don't have permission to do this.",
        )
    }
}
