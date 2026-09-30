package com.actionaura.retail.net

import com.google.common.truth.Truth.assertThat
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.ResponseBody.Companion.toResponseBody
import org.junit.Test
import retrofit2.HttpException
import retrofit2.Response
import java.io.IOException

/**
 * Pure-function proof for `apiErrorMessage`/`loginErrorMessage` (this file's
 * own [ApiErrors.kt] docstring), the single Throwable -> user-facing-message
 * mapping every screen's "call failed" catch block is supposed to go
 * through.
 *
 * Why a plain JVM test can prove this at all with no device attached: both
 * functions are pure (Throwable in, String out, no Context/Android
 * framework dependency beyond [com.actionaura.retail.ui.i18n.tr], which
 * defaults to English with no locale configured -- see AppLocale.lang's
 * initial value). retrofit2.HttpException is built the same way Retrofit
 * itself builds it on a real non-2xx response: [Response.error] wrapping a
 * real OkHttp [okhttp3.ResponseBody], so `e.response()?.errorBody()?.string()`
 * inside `serverMessageOf` reads a real body, not a mock.
 *
 * ── The bug these tests exist to catch ───────────────────────────────────────
 * LoginScreen.kt used to catch every login failure -- wrong password
 * included -- with a bare `catch (e: Exception)` and report the ONE fixed
 * string "Couldn't reach the server. Try again.", regardless of cause.
 * PROVEN on a real device: a healthy embedded backend (`GET /api/health` ->
 * 200) answered a mistyped password with `401
 * {"error":"Invalid email or password."}`, and the screen told the cashier
 * their network was down.
 */
class ApiErrorsTest {

    /** Builds the same [HttpException] Retrofit throws for a real non-2xx response. */
    private fun httpException(code: Int, body: String): HttpException {
        val responseBody = body.toResponseBody("application/json".toMediaType())
        return HttpException(Response.error<Any>(code, responseBody))
    }

    // ── apiErrorMessage: every OTHER screen's mapping ───────────────────────

    @Test
    fun a_401_on_an_already_authenticated_route_reads_as_session_expired() {
        // mt_login_required's shape (commercial_runtime/identity/mt_auth.py):
        // session cookie missing, expired, or revoked -- not a login attempt.
        val e = httpException(401, """{"error":"mt_login_required"}""")
        assertThat(apiErrorMessage(e)).isEqualTo("Your session has expired. Please log in again.")
    }

    @Test
    fun a_403_names_the_subscription_not_the_network() {
        // A message DISTINCT from the 403 branch's own hardcoded fallback
        // ("This action is not available in the current licensing state.")
        // -- deliberately, so this proves the server's body is actually
        // being read rather than coincidentally matching the fallback.
        //
        // FIXTURE CHANGED in the A2 fix round: the body now carries the
        // licence guard's real `reason_code` (flask_guard.py), because a 403
        // WITHOUT one is a permission refusal and no longer reads as licence.
        // What this test can no longer catch: a body-less/reason-less 403 being
        // called a licence problem -- that is now pinned the other way by
        // a_capability_403_says_permission_not_subscription below.
        val e = httpException(
            403,
            """{"status":"error","reason_code":"LICENSE_INACTIVE","message":"Your trial ended 3 days ago. Renew to keep selling."}""",
        )
        assertThat(apiErrorMessage(e))
            .isEqualTo("Blocked by your subscription/license: Your trial ended 3 days ago. Renew to keep selling.")
    }

    @Test
    fun a_licence_403_with_no_server_message_falls_back_to_the_generic_licensing_sentence() {
        val e = httpException(403, """{"status":"error","reason_code":"CAPABILITY_NOT_ENTITLED"}""")
        assertThat(apiErrorMessage(e))
            .isEqualTo("Blocked by your subscription/license: This action is not available in the current licensing state.")
    }

    @Test
    fun a_capability_403_says_permission_not_subscription() {
        // mt_auth._capability_denied_response's REAL body: error + message +
        // code, and NO reason_code. The old code told a cashier refused a
        // manager-only action to go and check their subscription.
        val e = httpException(
            403,
            """{"status":"error","error":"You do not have permission for this action. Ask your store administrator.","message":"You do not have permission for this action. Ask your store administrator.","code":403}""",
        )
        val msg = apiErrorMessage(e)
        assertThat(msg).isEqualTo("You don't have permission to do this.")
        assertThat(msg).doesNotContain("subscription")
    }

    @Test
    fun a_403_with_an_empty_body_is_a_permission_refusal_not_a_licence_one() {
        assertThat(apiErrorMessage(httpException(403, "")))
            .isEqualTo("You don't have permission to do this.")
    }

    @Test
    fun a_reason_code_that_is_not_a_non_blank_string_does_not_make_a_403_a_licence_one() {
        // The discriminator is a non-blank STRING reason_code, nothing looser.
        for (body in listOf(
            """{"reason_code":null,"message":"x"}""",
            """{"reason_code":"","message":"x"}""",
            """{"reason_code":403,"message":"x"}""",
        )) {
            assertThat(apiErrorMessage(httpException(403, body)))
                .isEqualTo("You don't have permission to do this.")
        }
    }

    @Test
    fun a_genuine_io_failure_still_reads_as_connectivity() {
        val e = IOException("Failed to connect to /127.0.0.1:5000")
        assertThat(apiErrorMessage(e)).isEqualTo("Couldn't reach the server")
    }

    @Test
    fun an_unexpected_500_names_the_status_rather_than_lying_about_the_network() {
        val e = httpException(500, "")
        val msg = apiErrorMessage(e)
        assertThat(msg).isEqualTo("Server error (HTTP 500)")
        assertThat(msg).doesNotContain("reach the server")
    }

    @Test
    fun a_503_with_a_server_message_shows_the_servers_own_words() {
        // The AI route's real 503 shape (net/ApiErrors.kt's own doc comment).
        val e = httpException(503, """{"success":false,"error":"AI assistant is temporarily unavailable."}""")
        assertThat(apiErrorMessage(e)).isEqualTo("AI assistant is temporarily unavailable.")
    }

    @Test
    fun a_non_http_throwable_says_so_rather_than_pretending_the_network_failed() {
        val e = IllegalStateException("boom")
        assertThat(apiErrorMessage(e)).isEqualTo("Unexpected error (IllegalStateException)")
    }

    // ── loginErrorMessage: the ONE 401 that means bad credentials ──────────

    @Test
    fun login_401_with_the_servers_real_message_reads_as_bad_credentials() {
        val e = httpException(401, """{"error":"Invalid email or password."}""")
        assertThat(loginErrorMessage(e)).isEqualTo("Invalid email or password.")
    }

    @Test
    fun login_401_with_no_body_still_falls_back_to_the_bad_credentials_wording() {
        val e = httpException(401, "")
        assertThat(loginErrorMessage(e)).isEqualTo("Invalid email or password.")
    }

    @Test
    fun login_401_extracts_a_DIFFERENT_server_message_when_one_is_sent() {
        // "Invalid email or password." IS mt_auth's real literal, which
        // means the test above alone can't tell "the body was actually
        // read" from "the fallback default happens to match it". A locked-
        // account response is a distinct, realistic message the same route
        // can send, so this proves real extraction rather than a
        // coincidence.
        val e = httpException(401, """{"error":"Account locked after too many failed attempts."}""")
        assertThat(loginErrorMessage(e)).isEqualTo("Account locked after too many failed attempts.")
    }

    @Test
    fun login_401_never_reads_as_a_stale_session_the_way_apiErrorMessage_alone_would() {
        // Two SEPARATE exceptions (same shape, own response bodies) rather
        // than one reused across both calls -- okhttp3.ResponseBody.string()
        // drains and closes its source, so a second read of the SAME
        // instance would see an empty body and silently prove nothing. No
        // real call site ever maps the same Throwable twice, so this
        // mirrors production, not just the assertion's convenience.
        val forLogin = httpException(401, """{"error":"Invalid email or password."}""")
        val forApi = httpException(401, """{"error":"Invalid email or password."}""")
        // The exact regression this function exists to prevent: apiErrorMessage's
        // OWN 401 branch (proven above) would say "session expired" here.
        assertThat(loginErrorMessage(forLogin)).isNotEqualTo(apiErrorMessage(forApi))
    }

    @Test
    fun login_io_failure_still_reads_as_connectivity_not_bad_credentials() {
        val e = IOException("timeout")
        assertThat(loginErrorMessage(e)).isEqualTo("Couldn't reach the server")
    }

    @Test
    fun login_403_delegates_to_the_same_subscription_wording_as_every_other_screen() {
        // Two separate exceptions -- see the identical note on the 401 test
        // above for why reusing one HttpException across both calls would
        // silently prove nothing (the second body read comes back empty).
        val forLogin = httpException(403, """{"reason_code":"LICENSE_INACTIVE","message":"Your subscription is restricted."}""")
        val forApi = httpException(403, """{"reason_code":"LICENSE_INACTIVE","message":"Your subscription is restricted."}""")
        assertThat(loginErrorMessage(forLogin)).isEqualTo(apiErrorMessage(forApi))
    }
}
