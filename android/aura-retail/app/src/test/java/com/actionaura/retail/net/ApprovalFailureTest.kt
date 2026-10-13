package com.actionaura.retail.net

import com.google.common.truth.Truth.assertThat
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.ResponseBody.Companion.toResponseBody
import org.junit.Test
import retrofit2.HttpException
import retrofit2.Response
import java.io.IOException

/**
 * ANDROID-3 item 2: the server's refusals around a manager's PIN, shown plainly - and the one decision
 * that depends on them ("ask a manager"). The bodies are the server's own (retail_api.py `_approval_error`,
 * `create_sale`): COPIED. A 403 here carries a STRING `code` and NO `reason_code`, which is exactly the
 * shape that used to fall through to the generic "You don't have permission".
 */
class ApprovalFailureTest {

    private fun http(code: Int, body: String) =
        HttpException(Response.error<Any>(code, body.toResponseBody("application/json".toMediaType())))

    @Test
    fun a_wrong_pin_says_so_and_is_not_a_permission_message() {
        val e = http(403, """{"status":"error","code":"APPROVAL_REJECTED","message":"That manager PIN was not accepted."}""")
        assertThat(apiErrorMessage(e)).isEqualTo("That manager PIN was not accepted.")
        assertThat(apiFailure(e).needsManager).isFalse()      // a rejected PIN is not an invitation to ask again by itself
    }

    @Test
    fun a_lockout_429_shows_the_servers_sentence() {
        val e = http(429, """{"status":"error","code":"PIN_LOCKED","message":"Too many wrong PINs for this manager. Try again in a few minutes.","data":{"retry_after_seconds":863}}""")
        assertThat(apiErrorMessage(e)).isEqualTo("Too many wrong PINs for this manager. Try again in a few minutes.")
    }

    @Test
    fun the_self_approval_and_price_refusals_read_plainly() {
        assertThat(apiErrorMessage(http(403, """{"status":"error","code":"APPROVER_IS_CASHIER","message":"A different person must approve this. Pick a manager."}""")))
            .isEqualTo("A different person must approve this. Pick a manager.")
        assertThat(apiErrorMessage(http(403, """{"status":"error","code":"PRICE_OVERRIDE_DENIED","message":"You are not allowed to change a price. Ask a manager to approve it."}""")))
            .isEqualTo("You are not allowed to change a price. Ask a manager to approve it.")
    }

    @Test
    fun only_the_two_ask_a_manager_refusals_open_the_pin_sheet() {
        val over = http(403, """{"status":"error","code":"DISCOUNT_OVER_LIMIT","message":"x","data":{"limit_pct":10,"requested_pct":20}}""")
        val price = http(403, """{"status":"error","code":"PRICE_OVERRIDE_DENIED","message":"You are not allowed to change a price. Ask a manager to approve it."}""")
        assertThat(apiFailure(over).needsManager).isTrue()
        assertThat(apiFailure(price).needsManager).isTrue()
        // deny half: a plain permission 403, a licence 403, a 400 and a network error do not
        assertThat(apiFailure(http(403, """{"status":"error","message":"nope","code":403}""")).needsManager).isFalse()
        assertThat(apiFailure(http(403, """{"status":"error","reason_code":"LICENSE_INACTIVE","message":"x"}""")).needsManager).isFalse()
        assertThat(apiFailure(http(400, """{"status":"error","code":"DISCOUNT_REASON_REQUIRED","message":"x"}""")).needsManager).isFalse()
        assertThat(apiFailure(IOException("boom")).needsManager).isFalse()
    }

    @Test
    fun asking_for_the_message_and_the_decision_from_one_exception_works_in_either_order() {
        // an error body can be read once; both answers must come from the same parse
        val e = http(403, """{"status":"error","code":"DISCOUNT_OVER_LIMIT","message":"x","data":{"limit_pct":10}}""")
        assertThat(apiErrorMessage(e)).isEqualTo("You can give at most 10% discount. Ask a manager for more.")
        assertThat(apiFailure(e).needsManager).isTrue()
        assertThat(apiFailure(e).message).isEqualTo("You can give at most 10% discount. Ask a manager for more.")
    }

    @Test
    fun an_unreachable_target_names_the_nearest_totals() {
        val e = http(400, """{"status":"error","code":"TARGET_NOT_REACHABLE","message":"That exact total cannot be reached. Pick one of the nearby totals.","data":{"nearest_below":9.99,"nearest_above":10.012}}""")
        assertThat(apiErrorMessage(e)).isEqualTo("That exact total cannot be reached. Nearest: JD 9.990 / JD 10.012")
        // with no numbers it falls back to the server's own sentence
        val bare = http(400, """{"status":"error","code":"TARGET_NOT_REACHABLE","message":"That exact total cannot be reached. Pick one of the nearby totals.","data":{"nearest_below":null,"nearest_above":null}}""")
        assertThat(apiErrorMessage(bare)).isEqualTo("That exact total cannot be reached. Pick one of the nearby totals.")
    }

    @Test
    fun a_missing_reason_is_the_servers_sentence() {
        val e = http(400, """{"status":"error","code":"DISCOUNT_REASON_REQUIRED","message":"Say why this discount is given (for example Damaged, Loyal customer, Round down)."}""")
        assertThat(apiErrorMessage(e)).isEqualTo("Say why this discount is given (for example Damaged, Loyal customer, Round down).")
    }
}
