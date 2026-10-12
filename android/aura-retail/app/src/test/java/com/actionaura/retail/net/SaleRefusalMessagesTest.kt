package com.actionaura.retail.net

import com.google.common.truth.Truth.assertThat
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.ResponseBody.Companion.toResponseBody
import org.junit.Test
import retrofit2.HttpException
import retrofit2.Response

/**
 * What the cashier READS when the server refuses a discount (ANDROID-2a item 3).
 *
 * The body below is copied from `create_sale`'s own refusal
 * (products/retail/backend/api/retail_api.py: "DISCOUNT_OVER_LIMIT"): HTTP 403,
 * `status: error`, a STRING `code`, the sentence, and `data.limit_pct`. It has
 * NO `reason_code`, which is exactly why it used to land in the generic
 * "You don't have permission to do this." branch -- a cashier told they lack
 * permission, with no hint that 5% was fine and 10% was not.
 */
class SaleRefusalMessagesTest {

    private fun httpException(code: Int, body: String): HttpException =
        HttpException(Response.error<Any>(code, body.toResponseBody("application/json".toMediaType())))

    @Test
    fun an_over_limit_discount_names_the_limit() {
        val e = httpException(403,
            """{"status":"error","code":"DISCOUNT_OVER_LIMIT",
                "message":"You can give at most 10% discount. Ask a manager for more.",
                "data":{"limit_pct":10.0,"requested_pct":15.0}}""")
        assertThat(apiErrorMessage(e)).isEqualTo("You can give at most 10% discount. Ask a manager for more.")
    }

    @Test
    fun a_fractional_limit_is_printed_the_way_a_person_says_it() {
        val e = httpException(403,
            """{"status":"error","code":"DISCOUNT_OVER_LIMIT","message":"x","data":{"limit_pct":7.5}}""")
        assertThat(apiErrorMessage(e)).isEqualTo("You can give at most 7.5% discount. Ask a manager for more.")
    }

    @Test
    fun an_over_limit_body_without_a_numeric_limit_falls_back_to_the_servers_sentence() {
        val e = httpException(403,
            """{"status":"error","code":"DISCOUNT_OVER_LIMIT","message":"Discount too large."}""")
        assertThat(apiErrorMessage(e)).isEqualTo("Discount too large.")
    }

    @Test
    fun a_plain_permission_refusal_is_still_a_permission_message() {
        // The deny half of the new branch: a 403 that is NOT the discount code must be untouched.
        val e = httpException(403, """{"status":"error","error":"forbidden","message":"No.","code":403}""")
        assertThat(apiErrorMessage(e)).isEqualTo("You don't have permission to do this.")
    }

    @Test
    fun a_licence_refusal_is_still_blamed_on_the_subscription() {
        val e = httpException(403,
            """{"status":"error","reason_code":"LICENSE_INACTIVE","message":"Licence inactive."}""")
        assertThat(apiErrorMessage(e)).startsWith("Blocked by your subscription/license:")
    }

    @Test
    fun a_tender_refusal_shows_the_servers_own_sentence_with_the_missing_amount() {
        // TENDERS_SHORT is a 400 whose message carries the exact figures; the cashier needs them.
        val msg = "The parts add up to 11.832 but 12.100 is due. 0.268 is still missing."
        val e = httpException(400, """{"status":"error","code":"TENDERS_SHORT","message":"$msg"}""")
        assertThat(apiErrorMessage(e)).isEqualTo(msg)
    }
}
