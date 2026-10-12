package com.actionaura.retail.net

import com.actionaura.retail.ui.pos.TenderPartMinor
import com.actionaura.retail.ui.pos.buildCreateSaleRequest
import com.google.common.truth.Truth.assertThat
import com.google.gson.Gson
import com.google.gson.JsonObject
import com.google.gson.JsonParser
import org.junit.Test

/**
 * ANDROID-2a item 2: the split-tender request against what the DESKTOP's
 * `_checkout` + `_buildTenderPayload` (frontend/subsystem-retail.js) send to the
 * same POST /sales. The desktop examples are COPIED and are the contract: the
 * brief for this wave said `payments`, the desktop actually sends `tenders`
 * with `payment_method: "split"` and deletes `amount_paid`, and the server
 * (create_sale: `'tenders' in data`) reads only that.
 */
class SplitTenderWireContractTest {

    private val gson = Gson()
    private fun json(o: Any): JsonObject = gson.toJsonTree(o).asJsonObject

    // card 5.000 + cash 10.000 with a 10% discount, as the desktop builds it.
    private val desktopSplitSale: JsonObject = JsonParser.parseString(
        """
        {"idempotency_key":"pos_1760000000000","customer_id":null,"subtotal":13.147,
         "discount_amount":1.315,"tax_amount":0,"total":11.832,"payment_method":"split",
         "points_redeemed":0,
         "items":[{"product_id":"p1","name":"Tea","quantity":2,"discount_pct":10}],
         "tenders":[{"method":"card","amount":5},{"method":"cash","amount":10}]}
        """.trimIndent()
    ).asJsonObject

    private val desktopCashSale: JsonObject = JsonParser.parseString(
        """
        {"idempotency_key":"pos_1760000000001","customer_id":null,"subtotal":5,"discount_amount":0,
         "tax_amount":0,"total":5,"amount_paid":5,"payment_method":"cash","points_redeemed":0,
         "items":[{"product_id":"p1","name":"Tea","quantity":2,"discount_pct":0}]}
        """.trimIndent()
    ).asJsonObject

    private val items = listOf(SaleItemReq("p1", 2.0, 10.0))

    @Test
    fun a_split_sale_has_the_desktops_shape_and_no_amount_paid() {
        val body = json(buildCreateSaleRequest(
            items = items, customerId = null, idempotencyKey = "k", pointsRedeemed = 0,
            singleMethod = "cash", creditDownPayment = null,
            splitParts = listOf(TenderPartMinor("card", 5000L), TenderPartMinor("cash", 10000L)), dp = 3,
        ))
        assertThat(body.get("payment_method").asString).isEqualTo("split")
        // The server derives paid / change / balance from the parts; the desktop deletes
        // amount_paid for a split and so must the phone.
        assertThat(body.has("amount_paid")).isFalse()
        assertThat(body.get("tenders")).isEqualTo(desktopSplitSale.get("tenders"))
        // Every key the phone sends exists on the desktop's payload (a subset, never an invention).
        assertThat(desktopSplitSale.keySet()).containsAtLeastElementsIn(body.keySet())
    }

    @Test
    fun a_single_method_sale_keeps_todays_shape_exactly() {
        val body = json(buildCreateSaleRequest(
            items = listOf(SaleItemReq("p1", 2.0, 0.0)), customerId = null, idempotencyKey = "k",
            pointsRedeemed = 0, singleMethod = "cash", creditDownPayment = null,
            splitParts = emptyList(), dp = 3,
        ))
        assertThat(body.has("tenders")).isFalse()
        assertThat(body.has("amount_paid")).isFalse()          // omitted, as before this wave
        assertThat(body.get("payment_method").asString).isEqualTo("cash")
        assertThat(desktopCashSale.keySet()).containsAtLeastElementsIn(body.keySet())
    }

    @Test
    fun one_part_is_a_single_method_sale_not_a_split() {
        val body = json(buildCreateSaleRequest(
            items = items, customerId = "c", idempotencyKey = "k", pointsRedeemed = 0,
            singleMethod = "card", creditDownPayment = null,
            splitParts = listOf(TenderPartMinor("card", 5000L)), dp = 3,
        ))
        assertThat(body.has("tenders")).isFalse()
        assertThat(body.get("payment_method").asString).isEqualTo("card")
    }

    @Test
    fun a_credit_down_payment_is_still_the_only_time_amount_paid_is_sent() {
        val body = json(buildCreateSaleRequest(
            items = items, customerId = "c", idempotencyKey = "k", pointsRedeemed = 0,
            singleMethod = "credit", creditDownPayment = 2.5, splitParts = emptyList(), dp = 3,
        ))
        assertThat(body.get("amount_paid").asDouble).isEqualTo(2.5)
    }

    @Test
    fun a_cash_part_carries_the_amount_handed_over_to_the_fil() {
        val body = json(buildCreateSaleRequest(
            items = items, customerId = null, idempotencyKey = "k", pointsRedeemed = 0,
            singleMethod = "cash", creditDownPayment = null,
            splitParts = listOf(TenderPartMinor("card", 5001L), TenderPartMinor("cash", 6831L)), dp = 3,
        ))
        val t = body.getAsJsonArray("tenders")
        assertThat(t[0].asJsonObject.get("amount").asDouble).isEqualTo(5.001)
        assertThat(t[1].asJsonObject.get("amount").asDouble).isEqualTo(6.831)
    }
}
