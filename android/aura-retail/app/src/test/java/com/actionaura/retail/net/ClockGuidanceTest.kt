package com.actionaura.retail.net

import com.actionaura.retail.ui.i18n.arabicFor
import com.google.common.truth.Truth.assertThat
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.ResponseBody.Companion.toResponseBody
import org.junit.Test
import retrofit2.HttpException
import retrofit2.Response

/**
 * r17 (owner decision 10A): the clock-moved-back stop must tell the shopkeeper
 * how to fix it, in English and Arabic, instead of only blocking.
 *
 * The server (flask_guard.py -> state_guidance.py) answers the refused sale with
 * a plain how-to-fix `message`; this pins that the phone shows it as written --
 * without the "Blocked by your subscription/license:" prefix, which would send
 * the owner to the wrong place -- and has an Arabic translation for it. The
 * contrast case (an ordinary licence refusal keeps the subscription prefix) is
 * in the same file so the prefix removal cannot quietly spread to every refusal.
 */
class ClockGuidanceTest {

    private fun httpException(code: Int, body: String): HttpException =
        HttpException(Response.error<Any>(code, body.toResponseBody("application/json".toMediaType())))

    private fun clockBody() =
        """{"status":"error","reason_code":"LICENSE_STATE_DENIED","message":"$CLOCK_REVIEW_GUIDANCE"}"""

    @Test
    fun the_clock_stop_shows_the_how_to_fix_sentence_without_the_subscription_prefix() {
        val msg = apiErrorMessage(httpException(403, clockBody()))
        assertThat(msg).isEqualTo(CLOCK_REVIEW_GUIDANCE)
        assertThat(msg).doesNotContain("subscription")
        assertThat(msg).contains("date and time")
    }

    @Test
    fun the_clock_sentence_has_an_arabic_translation_that_says_what_to_do() {
        val ar = arabicFor(CLOCK_REVIEW_GUIDANCE)
        assertThat(ar).isNotNull()
        assertThat(ar).contains("التاريخ والوقت")
        assertThat(ar).contains("بياناتك بأمان")
    }

    @Test
    fun an_ordinary_licence_refusal_keeps_the_subscription_prefix() {
        val e = httpException(403, """{"status":"error","reason_code":"LICENSE_INACTIVE","message":"Your trial ended 3 days ago. Renew to keep selling."}""")
        assertThat(apiErrorMessage(e))
            .isEqualTo("Blocked by your subscription/license: Your trial ended 3 days ago. Renew to keep selling.")
    }
}
