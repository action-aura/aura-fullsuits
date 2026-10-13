package com.actionaura.retail.ui.pos

import com.actionaura.retail.net.CashierLimitSettings
import com.actionaura.retail.net.limitPct
import com.actionaura.retail.ui.CAP_DISCOUNT
import com.actionaura.retail.ui.codeOnly
import com.google.common.truth.Truth.assertThat
import org.junit.Assert.assertEquals
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File

/**
 * The discount wiring, read from source (there is no Compose runner here), and
 * the two server facts the phone's copy of the rule depends on, read from the
 * Python so a rename on the server cannot leave the phone gating on a ghost.
 */
class DiscountWiringContractTest {

    private val moduleRoot = File(".")
    private val suiteRoot = File("../../..")

    private fun kotlin(relative: String): String {
        val f = File(moduleRoot, relative)
        assumeTrue("$relative not reachable from this run context", f.exists())
        return codeOnly(f.readText())
    }

    private fun python(relative: String): String {
        val f = File(suiteRoot, relative)
        assumeTrue("$relative not reachable from this run context", f.exists())
        return f.readText()
    }

    private val posSrc get() = kotlin("src/main/java/com/actionaura/retail/ui/screens/RetailScreens.kt")

    @Test
    fun the_sale_items_are_built_by_the_discount_rulebook_not_inline() {
        assertThat(posSrc).contains("buildSaleItems(cartLines, salePct, access, cartDiscountOn = activeDisc != null)")
        // The old inline mapping (no discount at all) must be gone.
        assertThat(posSrc).doesNotContain("SaleItemReq(id, qty)")
    }

    @Test
    fun the_access_is_resolved_from_the_session_capabilities_and_the_shop_limit() {
        assertThat(posSrc).contains("discountAccess(RetailSession.capabilities, cashierLimitPct)")
        assertThat(posSrc).contains("cashierDiscountLimit()")
    }

    @Test
    fun the_discount_controls_sit_behind_the_access_check() {
        // The whole-sale % box is inside `access !is DiscountAccess.None`.
        val box = posSrc.indexOf("SaleDiscountField(")
        val gateBefore = posSrc.lastIndexOf("if (access !is DiscountAccess.None)", box)
        assertThat(box).isGreaterThan(-1)
        assertThat(gateBefore).isGreaterThan(-1)
        assertThat(box - gateBefore).isLessThan(200)
        // ANDROID-3: the per-line tap target is no longer gated (a price of its own is offered to everyone who
        // can sell, and a manager's PIN is asked at Charge); the line menu gates only its % box, from `access`.
        assertThat(posSrc).contains("lineMenuKey = l.key")
        val menu = codeOnly(File(moduleRoot, "src/main/java/com/actionaura/retail/ui/screens/PosLineMenu.kt").readText())
        val pctBox = menu.indexOf("Item discount (%)")
        assertThat(menu.lastIndexOf("if (access !is DiscountAccess.None)", pctBox)).isGreaterThan(-1)
    }

    @Test
    fun a_successful_sale_clears_the_discount_state() {
        assertThat(posSrc).contains("cartLines = emptyList(); cartDisc = null; showCart = false; successSale = r.data")
        assertThat(posSrc).contains("salePctText = \"\" splitMode = false")
    }

    @Test
    fun the_phone_gates_on_the_same_capability_string_the_server_defines() {
        val server = Regex("""(?m)^CAP_DISCOUNT = '([^']+)'""")
            .find(python("commercial_runtime/identity/user_accounts.py"))?.groupValues?.get(1)
        assertEquals(server, CAP_DISCOUNT)
    }

    @Test
    fun the_server_still_reads_the_limit_from_the_key_the_phone_asks_for_and_refuses_with_the_code_the_phone_decodes() {
        val api = python("products/retail/backend/api/retail_api.py")
        assertThat(api).contains("'cashier_discount_limit_pct'")
        assertThat(api).contains("'code': 'DISCOUNT_OVER_LIMIT'")
        assertThat(api).contains("'limit_pct': limit_pct")
    }

    @Test
    fun the_limit_reads_safely() {
        assertEquals(10.0, CashierLimitSettings("10").limitPct(), 0.0)
        assertEquals(7.5, CashierLimitSettings(" 7.5 ").limitPct(), 0.0)
        assertEquals(0.0, CashierLimitSettings("0").limitPct(), 0.0)
        assertEquals(0.0, CashierLimitSettings("abc").limitPct(), 0.0)
        assertEquals(0.0, CashierLimitSettings(null).limitPct(), 0.0)
        assertEquals(0.0, (null as CashierLimitSettings?).limitPct(), 0.0)
        assertEquals(100.0, CashierLimitSettings("250").limitPct(), 0.0)
        assertEquals(0.0, CashierLimitSettings("-5").limitPct(), 0.0)
    }
}
