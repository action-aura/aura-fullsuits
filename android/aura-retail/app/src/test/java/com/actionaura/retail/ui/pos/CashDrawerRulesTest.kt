package com.actionaura.retail.ui.pos

import com.actionaura.retail.net.CashSession
import com.actionaura.retail.net.XReport
import com.google.common.truth.Truth.assertThat
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The drawer's phone-side rules: MONEY-4b accounts, the UI-2 blind count, and
 * the opening-float pre-fill. The expected sets are COPIED from the desktop
 * (frontend/cash-drawer.js PAID_OUT_PURPOSES / PAID_IN_PURPOSES) and the server
 * (PAID_OUT_ACCOUNT_CODES / PAID_IN_ACCOUNT_CODES); the contract test that
 * reads the Python is CashDrawerServerContractTest.
 */
class CashDrawerRulesTest {

    private val dp = 3

    // ── MONEY-4b: the required "What for?" ───────────────────────────────────

    @Test
    fun paid_out_and_paid_in_offer_exactly_the_desktops_accounts() {
        assertThat(PAID_OUT_PURPOSES.map { it.code })
            .containsExactly("6000", "6100", "6200", "6300", "6400", "6500", "6900", "3100").inOrder()
        assertThat(PAID_IN_PURPOSES.map { it.code }).containsExactly("4800", "3100").inOrder()
        assertThat(purposesFor(MovementType.FLOAT_IN)).isNull()
        assertThat(purposesFor(MovementType.FLOAT_OUT)).isNull()
    }

    @Test
    fun a_paid_out_without_a_purpose_is_refused_before_anything_is_sent() {
        assertEquals(MovementCheck.PurposeRequired, buildMovementRequest(MovementType.PAID_OUT, "5", "x", null, dp))
        assertEquals(MovementCheck.PurposeRequired, buildMovementRequest(MovementType.PAID_OUT, "5", "x", "", dp))
        assertEquals(MovementCheck.PurposeRequired, buildMovementRequest(MovementType.PAID_IN, "5", "x", null, dp))
    }

    @Test
    fun a_purpose_from_the_other_list_is_refused() {
        // 4800 is income: it can never be what a paid-OUT was for, and an expense code
        // can never be what a paid-IN was for (4900, sales returns, is on neither list).
        assertEquals(MovementCheck.PurposeRequired, buildMovementRequest(MovementType.PAID_OUT, "5", "", "4800", dp))
        assertEquals(MovementCheck.PurposeRequired, buildMovementRequest(MovementType.PAID_IN, "5", "", "6300", dp))
        assertEquals(MovementCheck.PurposeRequired, buildMovementRequest(MovementType.PAID_IN, "5", "", "4900", dp))
    }

    @Test
    fun owner_drawings_is_valid_both_ways_with_the_same_code() {
        val out = buildMovementRequest(MovementType.PAID_OUT, "5", "", "3100", dp) as MovementCheck.Ok
        val inn = buildMovementRequest(MovementType.PAID_IN, "5", "", "3100", dp) as MovementCheck.Ok
        assertEquals("3100", out.request.gl_account_code)
        assertEquals("3100", inn.request.gl_account_code)
    }

    @Test
    fun a_transport_paid_out_of_five_dinars_is_built_as_the_desktop_builds_it() {
        val ok = buildMovementRequest(MovementType.PAID_OUT, "5.000", "Transport", "6300", dp) as MovementCheck.Ok
        assertEquals("paid_out", ok.request.type)
        assertEquals(5.0, ok.request.amount, 0.0)
        assertEquals("6300", ok.request.gl_account_code)
        assertEquals("Transport", ok.request.reason)
    }

    @Test
    fun floats_send_no_account_and_need_no_purpose() {
        for (t in listOf(MovementType.FLOAT_IN, MovementType.FLOAT_OUT)) {
            val ok = buildMovementRequest(t, "20", "", "6300", dp) as MovementCheck.Ok
            assertNull(ok.request.gl_account_code)
        }
    }

    @Test
    fun a_bad_amount_is_refused() {
        for (bad in listOf("", "0", "-1", "abc", "1.2345", null)) {
            assertEquals("amount '$bad'", MovementCheck.BadAmount,
                buildMovementRequest(MovementType.FLOAT_IN, bad, "", null, dp))
        }
    }

    // ── UI-2: the blind count ────────────────────────────────────────────────

    @Test
    fun nothing_is_revealed_before_a_count_even_to_an_approver() {
        assertEquals(CloseReveal(false, false), closeReveal(hasCount = false, mayApprove = true))
        assertEquals(CloseReveal(false, false), closeReveal(hasCount = false, mayApprove = false))
    }

    @Test
    fun after_a_count_an_approver_sees_expected_and_variance_and_a_cashier_still_does_not() {
        assertEquals(CloseReveal(true, true), closeReveal(hasCount = true, mayApprove = true))
        assertEquals(CloseReveal(false, false), closeReveal(hasCount = true, mayApprove = false))
    }

    // ── pre-filled opening float ─────────────────────────────────────────────

    private fun s(counted: Double?, status: String?, mine: Boolean = true) =
        CashSession(id = "x", status = "closed", closing_float_counted = counted, variance_status = status, is_this_terminal = mine)

    @Test
    fun the_float_is_prefilled_from_this_terminals_latest_ended_drawers_count() {
        assertEquals(50.0, lastClosingCount(listOf(s(50.0, "unverified"), s(10.0, "approved")))!!, 0.0)
    }

    @Test
    fun another_terminals_count_a_still_open_drawer_and_a_never_counted_drawer_prefill_nothing() {
        assertNull(lastClosingCount(listOf(s(50.0, "unverified", mine = false))))
        assertNull(lastClosingCount(listOf(s(null, "pending"))))
        // Newest ended drawer was force-closed with no count: do NOT fall back to an older, stale count.
        assertNull(lastClosingCount(listOf(s(null, "not_counted"), s(40.0, "approved"))))
        assertNull(lastClosingCount(emptyList()))
    }

    // ── approving ────────────────────────────────────────────────────────────

    @Test
    fun only_an_approver_is_offered_drawers_to_accept() {
        val list = listOf(s(10.0, "unverified"), s(null, "not_counted"), s(5.0, "approved"),
            CashSession(id = "open", status = "open", variance_status = "pending"))
        assertTrue(approvableSessions(list, mayApprove = false).isEmpty())
        assertThat(approvableSessions(list, mayApprove = true).map { it.variance_status })
            .containsExactly("unverified", "not_counted")
    }

    // ── the X report keeps no answer a blind counter could read ─────────────

    @Test
    fun a_non_approvers_x_report_loses_expected_cash_and_variance_but_keeps_the_rest() {
        val r = XReport(opening_float = 20.0, cash_sales = 35.5, expected_cash = 55.5, variance = -1.0, variance_status = "unverified")
        val cashier = redactXReport(r, mayApprove = false)
        assertEquals(0.0, cashier.expected_cash, 0.0)
        assertNull(cashier.variance)
        assertEquals(35.5, cashier.cash_sales, 0.0)
        assertEquals(20.0, cashier.opening_float, 0.0)
        // allow half: an approver keeps everything.
        assertEquals(r, redactXReport(r, mayApprove = true))
    }
}
