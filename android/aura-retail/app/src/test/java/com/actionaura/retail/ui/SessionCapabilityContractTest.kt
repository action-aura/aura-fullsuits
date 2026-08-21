package com.actionaura.retail.ui

import com.actionaura.retail.net.SessionResponse
import com.actionaura.retail.net.User
import com.google.common.truth.Truth.assertThat
import com.google.gson.Gson
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File

/**
 * "A gate that never engages is indistinguishable from a gate on a permissive
 * account" -- app-shell.js:272, in a dated post-mortem about this exact bug,
 * one file over on the desktop shell.
 *
 * ── What was broken ──────────────────────────────────────────────────────────
 * `GET /api/auth/session` (commercial_runtime/identity/onboarding_routes.py
 * ::get_session) returns `capabilities` as a TOP-LEVEL key, a SIBLING of
 * `user` -- never a member of it. This client declared `capabilities` on the
 * `User` model, so parsing a real cashier body left `user.capabilities` null,
 * `RetailSession.capabilities` null, and `hasCapability("retail.reports")`
 * answering TRUE (it fails open on null, correctly and by design) for an
 * account granted only sell, refund and cash.close.
 *
 * The visible consequence on this client: EmployeeSalesScreen's capability gate
 * and ReportsScreen's By-Employee entry were both written, both shipped, and
 * neither has ever once refused anybody. The desktop paid for the identical
 * bug and its post-mortem ends with the instruction this file follows:
 * "anything that resolves this list needs its own test; this method cannot be
 * the place a mistake surfaces."
 *
 * The server key placement is the contract and is not changing, so the test for
 * it reads the Python rather than restating it.
 */
class SessionCapabilityContractTest {

    private val moduleRoot = File(".")
    private val suiteRoot = File("../../..")
    private val gson = Gson()

    private fun source(relative: String): String {
        val file = File(moduleRoot, relative)
        assumeTrue("$relative not reachable from this run context", file.exists())
        return file.readText()
    }

    private val sessionSrc get() = source("src/main/java/com/actionaura/retail/ui/RetailSession.kt")

    /**
     * A REAL cashier session body, copied verbatim out of app-shell.js's
     * post-mortem, which recorded it by booting the app and logging in as each
     * role rather than by re-reading the handler. That provenance is the point:
     * the bug survived a correct-looking fix precisely because everyone kept
     * checking the client against the client.
     */
    private val cashierBody = """
        {"authenticated": true,
         "capabilities": ["retail.cash.close", "retail.refund", "retail.sell"],
         "is_mt": true, "language": "en",
         "user": {"id": "u-1", "role": "cashier", "email": "sam@shop.test",
                  "employee_id": "EMP-0002", "clinic_role": ""}}
    """.trimIndent()

    // ── The server contract, read from the server ────────────────────────────

    @Test
    fun the_session_route_puts_capabilities_beside_user_and_not_inside_it() {
        val routes = File(suiteRoot, "commercial_runtime/identity/onboarding_routes.py")
        assumeTrue("onboarding_routes.py not reachable from this run context", routes.exists())
        val py = routes.readText()

        // The authenticated response literal: 'capabilities' is emitted, then
        // 'user': { ... } opens. Anything that moved the key inside `user`
        // would break this and should, because both clients read the sibling.
        val body = py.substringAfter("'authenticated': True,").substringBefore("return jsonify({'authenticated': False})")
        assertThat(body).contains("'capabilities': capabilities,")
        val userDict = body.substringAfter("'user': {")
        assertThat(userDict.substringBefore("}")).doesNotContain("capabilities")
    }

    // ── The client resolves it from the right place ──────────────────────────

    @Test
    fun a_real_cashier_body_resolves_the_three_grants_the_server_actually_sent() {
        val parsed = gson.fromJson(cashierBody, SessionResponse::class.java)

        // The bug, stated as the thing that must not be true again: reading it
        // off `user` yields nothing at all.
        assertThat(parsed.user?.capabilities).isNull()

        assertThat(sessionCapabilities(parsed))
            .containsExactly("retail.cash.close", "retail.refund", "retail.sell")
    }

    @Test
    fun that_cashier_is_refused_the_reports_screen() {
        // The consequence the whole feature exists for. This assertion is what
        // was silently false for the entire life of the capability gate on this
        // client: hasCapability answered TRUE for an account holding none of
        // the three codes that matter.
        val caps = sessionCapabilities(gson.fromJson(cashierBody, SessionResponse::class.java))
        assertThat(holdsCapability(caps, CAP_REPORTS)).isFalse()
        assertThat(holdsCapability(caps, "retail.sell")).isTrue()
    }

    @Test
    fun the_session_object_adopts_it_so_the_screens_can_see_it() {
        // Resolution is worthless if nothing stores it. RetailSession is the
        // Compose state every gated screen reads.
        try {
            RetailSession.adopt(gson.fromJson(cashierBody, SessionResponse::class.java))
            assertThat(RetailSession.hasCapability(CAP_REPORTS)).isFalse()
            assertThat(RetailSession.hasCapability("retail.sell")).isTrue()
            assertThat(RetailSession.isAdmin).isFalse()
        } finally {
            RetailSession.reset()
        }
    }

    @Test
    fun an_admin_body_still_gets_everything_the_server_grants_an_admin() {
        // _capabilities_for_session() returns all eight codes for role=admin,
        // deliberately reading the ROLE rather than the permission table. A
        // resolution that only worked for the restricted case would hide the
        // reports screen from the one account that certainly may see it.
        val adminBody = """
            {"authenticated": true,
             "capabilities": ["retail.cash.close", "retail.refund", "retail.reports", "retail.sell"],
             "is_mt": true, "language": "en",
             "user": {"id": "u-0", "role": "admin", "email": "owner@shop.test"}}
        """.trimIndent()
        try {
            RetailSession.adopt(gson.fromJson(adminBody, SessionResponse::class.java))
            assertThat(RetailSession.hasCapability(CAP_REPORTS)).isTrue()
            assertThat(RetailSession.isAdmin).isTrue()
        } finally {
            RetailSession.reset()
        }
    }

    @Test
    fun logging_out_puts_the_grant_list_back_to_not_known_and_the_next_account_starts_clean() {
        // [RetailSession] is a singleton holding Compose state, and a handset
        // running a shop is the one device where "the next person to sign in"
        // is not theoretical -- it is the next shift. A logout that left the
        // previous account's grants behind would hand them to whoever logs in
        // next, for as long as it takes /api/auth/session to answer.
        try {
            RetailSession.adopt(gson.fromJson(cashierBody, SessionResponse::class.java))
            assertThat(RetailSession.capabilities)
                .containsExactly("retail.cash.close", "retail.refund", "retail.sell")

            RetailSession.reset()

            // Back to NOT KNOWN (null, fails open), never to "holds nothing"
            // (empty, denies everything). Those are the two states this whole
            // file exists to keep apart, and logging out teaches us nothing
            // about the next account: clearing to `emptyList()` would blank
            // every gated screen for the next person until their session
            // resolved, which is an outage rather than a safeguard.
            assertThat(RetailSession.capabilities).isNull()
            assertThat(RetailSession.isAdmin).isFalse()
            assertThat(RetailSession.hasCapability(CAP_REPORTS)).isTrue()
        } finally {
            RetailSession.reset()
        }

        // ...and the logout path really calls it, inside the handler rather
        // than somewhere a future refactor can drop: state that is only
        // cleared in a test is not cleared.
        val logout = codeOnly(source("src/main/java/com/actionaura/retail/ui/AppRoot.kt"))
            .substringAfter("ApiClient.get().logout()")
        assertThat(logout.substringBefore("onLogout()")).contains("RetailSession.reset()")
    }

    // ── The three states the desktop deliberately keeps apart ────────────────

    @Test
    fun an_absent_capabilities_key_stays_unknown_and_keeps_failing_open() {
        // "/api/auth/session hasn't resolved yet" and "this backend predates
        // the field" are one state to this client and answer TRUE, exactly as
        // app-shell.js does. Making the fix fail CLOSED instead would blank a
        // gated screen for every role the day it shipped -- which is the
        // difference between an inert change and an outage.
        val noKey = gson.fromJson("""{"authenticated": true, "user": {"role": "cashier"}}""", SessionResponse::class.java)
        assertThat(sessionCapabilities(noKey)).isNull()
        assertThat(holdsCapability(sessionCapabilities(noKey), CAP_REPORTS)).isTrue()

        // An explicit JSON null is the same state, not a crash: Gson writes it
        // straight into the field regardless of the Kotlin type, so the field
        // has to admit it (see ByEmployeeResponse's post-mortem).
        val explicitNull = gson.fromJson("""{"authenticated": true, "capabilities": null}""", SessionResponse::class.java)
        assertThat(sessionCapabilities(explicitNull)).isNull()

        assertThat(sessionCapabilities(null)).isNull()
    }

    @Test
    fun an_empty_grant_list_is_a_resolved_answer_and_not_the_unknown_one() {
        // `[]` means "this account holds nothing" and must deny. Collapsing it
        // into the null bucket would grant everything to the most restricted
        // account in the shop -- and `[]` is truthy in the language the desktop
        // twin is written in, which is exactly how that trap gets set.
        val empty = gson.fromJson("""{"authenticated": true, "capabilities": []}""", SessionResponse::class.java)
        assertThat(sessionCapabilities(empty)).isEmpty()
        assertThat(holdsCapability(sessionCapabilities(empty), CAP_REPORTS)).isFalse()
    }

    @Test
    fun the_user_object_is_still_read_as_a_fallback_exactly_like_the_desktop() {
        // Not defensive padding. The clinic product shares this identity stack
        // and its session route is a separate code path, and `user` is the
        // obvious place for a future contributor to add the key. Reading both
        // costs one null check and removes a failure mode whose whole character
        // is that it is silent. Top-level wins when both are present.
        val onUser = SessionResponse(
            authenticated = true,
            user = User(role = "cashier", capabilities = listOf("retail.sell")),
        )
        assertThat(sessionCapabilities(onUser)).containsExactly("retail.sell")

        val both = SessionResponse(
            authenticated = true,
            capabilities = listOf(CAP_REPORTS),
            user = User(role = "cashier", capabilities = listOf("retail.sell")),
        )
        assertThat(sessionCapabilities(both)).containsExactly(CAP_REPORTS)
    }

    @Test
    fun the_resolution_lives_in_one_named_place_the_way_the_desktop_shell_learned_to() {
        // app-shell.js pulled this out of init() into its own method for one
        // stated reason: it is the single line whose correctness cannot be
        // established by reading it, because the question is about a different
        // file in a different language. A copy inlined at a call site is how
        // the desktop's version stayed wrong through a shipped fix.
        val code = codeOnly(sessionSrc)
        assertThat(code).contains("fun sessionCapabilities(session: SessionResponse?)")

        val appRoot = codeOnly(source("src/main/java/com/actionaura/retail/ui/AppRoot.kt"))
        assertThat(appRoot).contains("RetailSession.adopt(session)")
        assertThat(appRoot).doesNotContain("session.capabilities")
    }
}
