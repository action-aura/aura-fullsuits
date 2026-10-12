package com.actionaura.retail.net

import com.actionaura.retail.ui.pos.MovementCheck
import com.actionaura.retail.ui.pos.MovementType
import com.actionaura.retail.ui.pos.buildMovementRequest
import com.google.common.truth.Truth.assertThat
import com.google.gson.Gson
import com.google.gson.JsonObject
import com.google.gson.JsonParser
import org.junit.Test

/**
 * ANDROID-2a item 4: drawer request bodies against what the DESKTOP's
 * cash-drawer.js sends to the same routes. The examples are COPIED from
 * `_submitMovement` / `_submitOpenShift` / `_submitClose` and are the contract.
 */
class CashMovementWireContractTest {

    private val gson = Gson()
    private fun json(o: Any): JsonObject = gson.toJsonTree(o).asJsonObject

    // "Cash Out 5.000, What for? Transport and delivery, note Transport" as the desktop sends it.
    private val desktopPaidOut: JsonObject = JsonParser.parseString(
        """{"type":"paid_out","amount":5,"reason":"Transport","gl_account_code":"6300"}"""
    ).asJsonObject
    private val desktopPaidIn: JsonObject = JsonParser.parseString(
        """{"type":"paid_in","amount":12.5,"reason":"","gl_account_code":"4800"}"""
    ).asJsonObject
    private val desktopFloatIn: JsonObject = JsonParser.parseString(
        """{"type":"float_in","amount":20,"reason":""}"""
    ).asJsonObject
    private val desktopOpen: JsonObject = JsonParser.parseString("""{"opening_float":50}""").asJsonObject
    private val desktopClose: JsonObject = JsonParser.parseString("""{"closing_float_counted":48.5}""").asJsonObject

    @Test
    fun a_paid_out_is_the_desktops_body_with_the_what_for_account() {
        val ok = buildMovementRequest(MovementType.PAID_OUT, "5.000", "Transport", "6300", 3) as MovementCheck.Ok
        assertThat(json(ok.request)).isEqualTo(desktopPaidOut)
    }

    @Test
    fun a_paid_in_is_the_desktops_body_with_the_income_account() {
        val ok = buildMovementRequest(MovementType.PAID_IN, "12.5", "", "4800", 3) as MovementCheck.Ok
        assertThat(json(ok.request)).isEqualTo(desktopPaidIn)
    }

    @Test
    fun a_float_sends_no_account_key_at_all() {
        val ok = buildMovementRequest(MovementType.FLOAT_IN, "20", "", "6300", 3) as MovementCheck.Ok
        assertThat(json(ok.request)).isEqualTo(desktopFloatIn)
        assertThat(json(ok.request).has("gl_account_code")).isFalse()
    }

    @Test
    fun open_and_close_bodies_are_the_desktops() {
        assertThat(json(OpenCashSessionRequest(50.0))).isEqualTo(desktopOpen)
        assertThat(json(CloseCashSessionRequest(48.5))).isEqualTo(desktopClose)
    }

    @Test
    fun approving_sends_an_empty_body_unless_acknowledging_an_uncounted_drawer() {
        // Desktop: {} normally, {acknowledge_uncounted: true} for a never-counted drawer.
        assertThat(json(ApproveCashSessionRequest()).keySet()).isEmpty()
        assertThat(json(ApproveCashSessionRequest(true)).get("acknowledge_uncounted").asBoolean).isTrue()
    }

    @Test
    fun a_real_x_report_body_parses_into_the_phones_model() {
        // Shape of GET x-report's `data` (retail_api.py::_cash_session_report).
        val body = """
            {"session_id":"s1","status":"open","opening_float":20.0,"cash_sales":35.5,
             "tender_totals":{"cash":35.5,"card":12.0},"customer_cash_in":0.0,"cash_refunds":1.0,
             "movements":{"float_in":0.0,"float_out":0.0,"paid_in":0.0,"paid_out":5.0},
             "supplier_payments":{"paid":0.0,"returned":0.0,"items":[]},"expected_cash":49.5,
             "window_start":"2026-10-12 09:00:00","window_end":null}
        """.trimIndent()
        val r = gson.fromJson(body, XReport::class.java)
        assertThat(r.expected_cash).isEqualTo(49.5)
        assertThat(r.movements.paid_out).isEqualTo(5.0)
        assertThat(r.tender_totals["card"]).isEqualTo(12.0)
    }

    @Test
    fun a_real_session_row_parses_including_the_variance_words() {
        val body = """
            {"id":"s1","status":"closed","opening_float":20.0,"closing_float_counted":48.5,
             "closing_float_expected":49.5,"variance":-1.0,"variance_status":"unverified",
             "is_this_terminal":true,"terminal_short":"ab12","ended_at":"2026-10-12 18:00:00"}
        """.trimIndent()
        val s = gson.fromJson(body, CashSession::class.java)
        assertThat(s.variance_status).isEqualTo("unverified")
        assertThat(s.is_this_terminal).isTrue()
        assertThat(s.closing_float_counted).isEqualTo(48.5)
    }
}
