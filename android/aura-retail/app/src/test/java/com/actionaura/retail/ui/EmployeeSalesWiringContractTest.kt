package com.actionaura.retail.ui

import com.actionaura.retail.net.EmployeeSales
import com.actionaura.retail.net.PaymentMethodStat
import com.actionaura.retail.net.PaymentMethodsResponse
import com.actionaura.retail.net.ByEmployeeResponse
import com.actionaura.retail.net.Sale
import com.actionaura.retail.ui.screens.Attribution
import com.actionaura.retail.ui.screens.attributedName
import com.actionaura.retail.ui.screens.attributionOf
import com.actionaura.retail.ui.screens.bidiIsolate
import com.actionaura.retail.ui.screens.byEmployeeRefusalOrNull
import com.actionaura.retail.ui.screens.isMoneyOut
import com.actionaura.retail.ui.screens.missingEndpointKeyOrNull
import com.actionaura.retail.ui.screens.rowAttribution
import com.actionaura.retail.ui.screens.saleAttribution
import com.google.common.truth.Truth.assertThat
import com.google.gson.Gson
import okhttp3.MediaType.Companion.toMediaTypeOrNull
import okhttp3.ResponseBody.Companion.toResponseBody
import org.junit.Assume.assumeTrue
import org.junit.Test
import retrofit2.HttpException
import retrofit2.Response
import java.io.File
import java.io.IOException

/**
 * Reachability + contract guards for the by-employee takings report (the
 * Android half of the v13 attribution columns).
 *
 * Same shape and same justification as EmployeesWiringContractTest: no Compose
 * test runner, no Chaquopy and no device here, but "is this screen actually in
 * the nav graph, and does anything actually navigate to it" is the regression
 * that stays invisible until a customer goes looking for a feature that was
 * written and never wired. SettingsScreen.kt in this very module is a
 * complete, working screen that has never been registered on any route, so it
 * has never rendered for a single user -- both halves are pinned below for
 * exactly that reason.
 *
 * The other half of this file is about HONESTY. The v13 migration
 * (products/retail/backend/database/schema.py) deliberately left
 * `actor_user_uid` NULL on every row that predates it -- "this device cannot
 * prove it is the terminal that rang a sale from before the column existed" --
 * and refused to guess an actor from the free-text `cashier` column, because a
 * wrong name on a sale is worse than no name. A client that renders those rows
 * as though somebody had been identified throws that refusal away at the last
 * step, so the rendering rules are pinned here too.
 */
class EmployeeSalesWiringContractTest {

    private val moduleRoot = File(".")
    // Gradle unit tests run with the module directory (android/aura-retail/app)
    // as the working directory -- see ReadinessContractTest -- so three levels
    // up is the aura-fullsuits root.
    private val suiteRoot = File("../../..")
    private val gson = Gson()

    private fun source(relative: String): String {
        val file = File(moduleRoot, relative)
        assumeTrue("$relative not reachable from this run context", file.exists())
        return file.readText()
    }

    private val appRoot get() = source("src/main/java/com/actionaura/retail/ui/AppRoot.kt")
    private val extraScreens get() = source("src/main/java/com/actionaura/retail/ui/screens/RetailExtraScreens.kt")
    private val screen get() = source("src/main/java/com/actionaura/retail/ui/screens/EmployeeSalesScreen.kt")
    private val strings get() = source("src/main/java/com/actionaura/retail/ui/i18n/Strings.kt")
    private val sessionSrc get() = source("src/main/java/com/actionaura/retail/ui/RetailSession.kt")

    private val catalog get() = literalRuns(codeOnly(strings)).toSet()

    // ── Reachability: BOTH halves ────────────────────────────────────────────

    @Test
    fun the_by_employee_route_is_registered_in_the_nav_graph() {
        val graph = appRoot.substringAfter("private fun retailGraph(")
        assertThat(graph).contains("""b.composable("employee_sales")""")
        assertThat(graph).contains("EmployeeSalesScreen(")
    }

    @Test
    fun something_actually_navigates_to_the_by_employee_route() {
        // A registered route with no caller is still dead code -- the half of
        // the SettingsScreen failure people forget, and the half every test
        // passes without. The entry lives in ReportsScreen, which is itself a
        // MoreScreen destination, so this is a path a user can walk:
        // More -> Reports -> By Employee.
        assertThat(extraScreens).contains("""onNavigate("employee_sales")""")
        // ...and the caller has to actually be handed a navigator, or the
        // lambda above is a parameter nobody ever supplies.
        assertThat(codeOnly(appRoot)).contains("ReportsScreen(snackbar, onNavigate =")
    }

    @Test
    fun the_screen_has_a_title_in_the_top_bar_map() {
        // Without an entry here the top bar falls through to "Action Aura",
        // so the user cannot tell which screen they are on.
        assertThat(appRoot).contains(""""employee_sales" -> "By Employee"""")
    }

    // ── Capability gating: the same gate the desktop uses ────────────────────

    @Test
    fun the_reports_entry_is_capability_gated_and_the_screen_re_checks() {
        // Hiding the entry is usability; the screen's own check is what makes a
        // stale session or a future deep link honest rather than a wall of
        // 403s. Both halves have to be present -- exactly the pairing
        // EmployeesWiringContractTest pins for the owner-only screen.
        val code = codeOnly(extraScreens)
        assertThat(code).contains("RetailSession.hasCapability(CAP_REPORTS)")
        assertThat(codeOnly(screen)).contains("if (!RetailSession.hasCapability(CAP_REPORTS))")
    }

    @Test
    fun the_capability_code_is_the_one_the_server_actually_gates_on() {
        // Read out of the Python that DEFINES it rather than restated here, so
        // this fails the day the constant moves or is renamed. A capability
        // string that no longer matches the server's does not fail loudly --
        // it silently hides a screen from everyone, or shows it to everyone.
        val accounts = File(suiteRoot, "commercial_runtime/identity/user_accounts.py")
        assumeTrue("user_accounts.py not reachable from this run context", accounts.exists())
        val match = Regex("""CAP_REPORTS\s*=\s*'([^']+)'""").find(accounts.readText())
        assertThat(match).isNotNull()
        assertThat(codeOnly(sessionSrc))
            .contains("""const val CAP_REPORTS = "${match!!.groupValues[1]}"""")
    }

    @Test
    fun an_unresolved_capability_set_fails_open_exactly_like_the_desktop_shell() {
        // app-shell.js's hasCapability() answers TRUE when `capabilities` is
        // not an array, covering two states it deliberately treats the same:
        // "/api/auth/session hasn't resolved yet" and "the backend hasn't
        // shipped the field yet". That is the opposite of every real
        // authorization check in this codebase and is only safe because
        // rendering advice failing open just means a tile renders and its own
        // fetch 403s. The two clients have to agree, or the same account sees
        // different screens on the desktop and on the handset.
        val shell = File(suiteRoot, "products/retail/frontend/app-shell.js")
        assumeTrue("app-shell.js not reachable from this run context", shell.exists())
        // Matched as a whitespace-tolerant pattern rather than an exact line:
        // the point of coupling to that file is its SEMANTICS, and a guard
        // that also breaks on a reformat is a guard somebody eventually mutes.
        assertThat(
            Regex("""!\s*Array\.isArray\(\s*this\.capabilities\s*\)\s*\)\s*return\s+true""")
                .containsMatchIn(shell.readText()),
        ).isTrue()

        assertThat(holdsCapability(null, CAP_REPORTS)).isTrue()
    }

    @Test
    fun a_resolved_capability_set_is_honoured_in_both_directions() {
        assertThat(holdsCapability(listOf("retail.sell", CAP_REPORTS), CAP_REPORTS)).isTrue()
        assertThat(holdsCapability(listOf("retail.sell"), CAP_REPORTS)).isFalse()
        // An empty array is a RESOLVED answer meaning "this account holds
        // nothing", and must not be confused with the unresolved null above.
        assertThat(holdsCapability(emptyList(), CAP_REPORTS)).isFalse()
    }

    // ── The Gson explicit-null crash ─────────────────────────────────────────

    @Test
    fun gson_overwrites_a_defaulted_non_null_list_with_a_real_null() {
        // The crash this module already paid for once, reproduced against a
        // model that is STILL declared the dangerous way, so the hazard is
        // demonstrated rather than asserted. Gson (converter-gson 2.11.0)
        // constructs a Kotlin data class whose parameters all have defaults
        // via the synthetic no-arg constructor -- so `data` really does start
        // as emptyList() -- and then ReflectiveTypeAdapterFactory writes the
        // explicit JSON null straight into the field, because the field is not
        // primitive. Kotlin's non-null type is a compile-time claim; nothing
        // enforces it at the field level.
        val parsed = gson.fromJson("""{"success":true,"data":null}""", PaymentMethodsResponse::class.java)
        val actual: List<PaymentMethodStat>? = parsed.data
        assertThat(actual).isNull()
    }

    @Test
    fun the_by_employee_body_is_declared_nullable_so_the_null_cannot_be_ignored() {
        // Same JSON, new model. Declaring the field nullable is what turns the
        // silent assignment above into something Kotlin forces the caller to
        // handle: there is no longer a lie in the type, so the load path
        // cannot walk past it into an isEmpty() that throws OUTSIDE the
        // try/catch as a bare NullPointerException.
        val parsed = gson.fromJson("""{"success":true,"data":null}""", ByEmployeeResponse::class.java)
        assertThat(parsed.success).isTrue()
        assertThat(parsed.data).isNull()

        // An ABSENT key reaches the same place, which is the point: both are
        // malformed for a 200 that claims success, and both take the error
        // path rather than one of them rendering as "no sales".
        assertThat(gson.fromJson("""{"success":true}""", ByEmployeeResponse::class.java).data).isNull()

        // ...and a well-formed body still parses, so the guard above is not
        // simply refusing everything.
        val ok = gson.fromJson(
            """{"success":true,"data":[{"actor_user_uid":"u1","email":"sam@shop.test","revenue":10.5,"transactions":2,"avg_ticket":5.25}]}""",
            ByEmployeeResponse::class.java,
        )
        assertThat(ok.data).hasSize(1)
        assertThat(ok.data!![0].email).isEqualTo("sam@shop.test")
    }

    // ── The envelope discriminator ───────────────────────────────────────────

    @Test
    fun a_two_hundred_that_says_no_is_not_rendered_as_an_empty_shop() {
        // `reportByEmployee(days).data ?: throw` consulted the body's PAYLOAD
        // and never its VERDICT, so a 200 carrying {"success": false, "data":
        // []} took the happy path and drew "No sales in this period" -- a
        // refusal shown as an empty shop, which is the single worst thing a
        // takings report can say. Four other screens in this module already
        // check the discriminator; this one deviated from its own module.
        assertThat(byEmployeeRefusalOrNull(
            ByEmployeeResponse(success = false, error = "Reports access required", data = emptyList()),
        )).isEqualTo("Reports access required")

        // The desktop envelope, which is the shape this route now answers with
        // (see the route's pinned contract): `status` is the discriminator the
        // desktop hard-gates on, and it wins when present.
        assertThat(byEmployeeRefusalOrNull(
            ByEmployeeResponse(status = "error", message = "Tenant context missing."),
        )).isEqualTo("Tenant context missing.")

        // A refusal that names no reason still has to say SOMETHING, and that
        // something must be translatable -- an empty error string rendering as
        // a blank line reads as the screen failing, not as the server refusing.
        val silent = byEmployeeRefusalOrNull(ByEmployeeResponse(status = "error"))
        assertThat(silent).isNotNull()
        assertThat(catalog).contains(silent)
        assertThat(byEmployeeRefusalOrNull(ByEmployeeResponse(success = false, error = "   ")))
            .isEqualTo(silent)
    }

    @Test
    fun both_envelope_spellings_are_accepted_so_neither_client_reads_a_success_as_a_refusal() {
        // The route's contract carries BOTH discriminators -- `status` for the
        // desktop, which hard-gates on it, and `success` for the five sibling
        // report routes and this client. Reading only one of them would turn
        // whichever spelling the backend settles on into a permanent refusal
        // screen, so both are honoured, with `status` taking precedence
        // because the desktop shape is the one that won.
        assertThat(byEmployeeRefusalOrNull(
            ByEmployeeResponse(status = "success", success = true, data = emptyList()),
        )).isNull()
        assertThat(byEmployeeRefusalOrNull(
            ByEmployeeResponse(status = "success", data = emptyList()),
        )).isNull()
        assertThat(byEmployeeRefusalOrNull(
            ByEmployeeResponse(success = true, data = emptyList()),
        )).isNull()

        // A blank/absent `status` is not a verdict -- fall through to `success`
        // rather than treating "" as "not success" and refusing every reply.
        assertThat(byEmployeeRefusalOrNull(
            ByEmployeeResponse(status = "", success = true, data = emptyList()),
        )).isNull()
    }

    @Test
    fun the_load_path_actually_consults_the_verdict_before_the_payload() {
        val code = codeOnly(screen)
        assertThat(code).contains("byEmployeeRefusalOrNull(")
        // Order matters: a refusal must be reported as a refusal, not as a
        // malformed body, so the verdict is read before `data` is unwrapped.
        assertThat(code.indexOf("byEmployeeRefusalOrNull("))
            .isLessThan(code.indexOf("?: throw"))
    }

    @Test
    fun the_response_model_carries_both_discriminators_and_both_error_spellings() {
        // The server's two error envelopes in this codebase are
        // {"status":"error","message":...} and {"success":false,"error":...}
        // (net/ApiErrors.kt decodes both for HTTP failures). A 200-with-a-
        // refusal has to be decodable through the same two spellings, or the
        // reason reaches the user as a blank.
        val parsed = gson.fromJson(
            """{"status":"success","success":true,"data":[],"message":null,"error":null}""",
            ByEmployeeResponse::class.java,
        )
        assertThat(parsed.status).isEqualTo("success")
        assertThat(parsed.success).isTrue()
        assertThat(byEmployeeRefusalOrNull(parsed)).isNull()
    }

    @Test
    fun a_null_body_is_refused_rather_than_rendered_as_no_sales() {
        // Avoiding the crash is not enough on its own: turning a malformed 200
        // into an empty list would trade an NPE for a screen that tells an
        // owner nobody sold anything today. The load path has to throw into
        // its own catch so the failure reaches the shared error mapping like
        // every other failure on this screen.
        val code = codeOnly(screen)
        assertThat(code).contains("?: throw")
        assertThat(code).contains("Malformed response")
    }

    // ── Error handling ───────────────────────────────────────────────────────

    @Test
    fun failures_go_through_the_shared_mapping_not_a_blanket_message() {
        // net/ApiErrors.kt exists because screens reported licensing 403s as
        // connectivity failures. A capability 403, a licence 403 and a dead
        // network are three different things an owner needs told apart.
        val src = codeOnly(screen)
        assertThat(src).doesNotContain("Couldn't reach the server")
        val catches = Regex("""catch\s*\(\s*\w+\s*:\s*Exception\s*\)""").findAll(src).toList()
        assertThat(catches).isNotEmpty()
        for (c in catches) {
            val body = src.substring(c.range.last, minOf(src.length, c.range.last + 160))
            assertThat(body).contains("apiErrorMessage(")
        }
    }

    @Test
    fun a_failed_load_is_not_rendered_as_an_empty_employee_list() {
        // Collapsing "the call failed" into "nobody sold anything" is how a
        // screen tells an owner their day's takings were zero because a
        // session expired. The error branch must be distinct, must come first,
        // and must show the mapped message.
        val src = screen
        assertThat(src).contains("loadError")
        assertThat(src.indexOf("loadError != null ->"))
            .isLessThan(src.indexOf("rows.isEmpty() ->"))
        assertThat(src.substringAfter("rows.isEmpty() -> EmptyState")).isNotEmpty()
    }

    @Test
    fun a_missing_endpoint_says_so_instead_of_reading_as_a_server_error() {
        // The route this screen calls is the newest thing in the retail API.
        // An install whose embedded server predates it answers 404, which the
        // shared mapping can only render as "Server error (HTTP 404)" -- true,
        // useless, and indistinguishable from a real fault. 404 on THIS path
        // has exactly one meaning, so it gets said.
        val notThere = HttpException(
            Response.error<Any>(404, "".toResponseBody("text/html".toMediaTypeOrNull())),
        )
        assertThat(missingEndpointKeyOrNull(notThere)).isNotNull()
        assertThat(catalog).contains(missingEndpointKeyOrNull(notThere))

        // Everything else keeps falling through to the shared mapping. A 403
        // is the capability guard and a 500 is a fault; neither is "this
        // build is old", and claiming otherwise would hide a real refusal.
        val forbidden = HttpException(
            Response.error<Any>(403, "".toResponseBody("application/json".toMediaTypeOrNull())),
        )
        assertThat(missingEndpointKeyOrNull(forbidden)).isNull()
        assertThat(missingEndpointKeyOrNull(IOException("no route to host"))).isNull()
    }

    // ── Honest attribution: never a guess ────────────────────────────────────

    @Test
    fun a_row_with_no_actor_is_reported_as_unattributed_never_as_a_name() {
        // v13 left actor_user_uid NULL on every pre-existing row on purpose.
        // Those takings are real and must still be counted, but naming anybody
        // for them would be the fabrication the migration refused to commit.
        val historical = EmployeeSales(revenue = 900.0, transactions = 9)
        assertThat(rowAttribution(historical)).isEqualTo(Attribution.NOT_RECORDED)
        assertThat(attributedName(historical.employee_id, historical.email)).isNull()
    }

    @Test
    fun a_row_whose_account_no_longer_resolves_is_not_collapsed_into_unattributed() {
        // "this sale was attributed, and the account has since been removed"
        // and "nobody was ever recorded" are different facts. Merging them
        // would quietly relabel a deleted employee's takings as history, and
        // an owner auditing a leaver would find nothing.
        val orphan = EmployeeSales(actor_user_uid = "2f1c…", revenue = 40.0, transactions = 1)
        assertThat(rowAttribution(orphan)).isEqualTo(Attribution.ACCOUNT_GONE)

        // A uid that is present but blank is not a uid.
        assertThat(rowAttribution(EmployeeSales(actor_user_uid = "   ")))
            .isEqualTo(Attribution.NOT_RECORDED)
    }

    @Test
    fun a_name_arriving_without_a_uid_is_never_printed_as_a_person() {
        // attributionOf() never consulted `uid` on its NAMED branch, so a row
        // carrying (actor_user_uid = null, email = "sam@shop.test") resolved
        // NAMED. The null-uid row IS the unattributed aggregate bucket -- every
        // sale rung before v13 existed, summed -- so if the route ever
        // populates an identity field on it, this screen prints a real person's
        // name over sales nobody was recorded for. That is precisely the
        // fabrication the v13 migration refused to commit, arriving at the last
        // step instead of the first.
        //
        // NAMED now requires BOTH: a uid that was actually recorded, AND a name
        // that actually resolved.
        assertThat(attributionOf(null, null, "sam@shop.test")).isEqualTo(Attribution.NOT_RECORDED)
        assertThat(attributionOf(null, "EMP-0002", null)).isEqualTo(Attribution.NOT_RECORDED)
        assertThat(attributionOf("   ", "EMP-0002", "sam@shop.test")).isEqualTo(Attribution.NOT_RECORDED)

        // Same rule through both call sites, so a single sale cannot disagree
        // with the report row it appears in.
        assertThat(rowAttribution(EmployeeSales(email = "sam@shop.test", revenue = 900.0)))
            .isEqualTo(Attribution.NOT_RECORDED)
        assertThat(saleAttribution(Sale(id = 1, actor_email = "sam@shop.test")))
            .isEqualTo(Attribution.NOT_RECORDED)
    }

    @Test
    fun a_wire_row_that_really_carries_a_name_on_a_null_uid_is_still_unattributed() {
        // A body off the wire, not a hand-built model, and the null-uid row
        // deliberately carries EVERY identity field this route can emit:
        // `employee_name` (the pre-formatted display string retail_api.py's
        // _resolve_actor_identities builds for the desktop) plus the two raw
        // columns this client reads. That is what makes it the hard question --
        // an all-null fixture resolves NOT_RECORDED for the trivial reason that
        // there is nothing to print, and would pass against the broken
        // name-first ordering too.
        val body = """
            {"status": "success", "success": true,
             "data": [{"actor_user_uid": "1f5d0e2c-…", "employee_id": "EMP-0002",
                       "email": "sam@shop.test", "employee_name": "sam@shop.test",
                       "revenue": 120.5, "transactions": 3, "avg_ticket": 40.17},
                      {"actor_user_uid": null, "employee_id": "EMP-0007",
                       "email": "sara@shop.test", "employee_name": "Sara Haddad",
                       "revenue": 900.0, "transactions": 9, "avg_ticket": 100.0}]}
        """.trimIndent()

        val rows = gson.fromJson(body, ByEmployeeResponse::class.java).data
        assertThat(rows).isNotNull()
        assertThat(rows!!).hasSize(2)

        // Guards the fixture. If the model ever stopped carrying `email`, the
        // verdict below would go green for the WRONG reason -- unattributed
        // because nothing arrived, rather than because no uid was recorded --
        // and this test would quietly stop asking anything.
        assertThat(rows[1].actor_user_uid).isNull()
        assertThat(attributedName(rows[1].employee_id, rows[1].email)).isEqualTo("sara@shop.test")

        // The verdict: a name with no uid behind it is never a person. That
        // null-uid row is not one sale -- it is the AGGREGATE of every sale
        // rung before v13 added the column, so a name landing on it hands the
        // shop's whole pre-attribution history to one employee.
        assertThat(rowAttribution(rows[1])).isEqualTo(Attribution.NOT_RECORDED)

        // ...and the rule is "no uid, no name", not "no names": the row that
        // did record one is still named.
        assertThat(rowAttribution(rows[0])).isEqualTo(Attribution.NAMED)
    }

    @Test
    fun the_desktop_classifier_consults_the_uid_before_the_name_exactly_as_this_one_does() {
        // Read out of the OTHER client, because "the two agree" is not a fact
        // about this file and cannot be established by reading it. The row
        // {actor_user_uid: null, employee_name: "Sara Haddad"} used to resolve
        // 'named' on the desktop and NOT_RECORDED here: two clients reading one
        // response and naming two different people. The lead settled it in this
        // client's favour -- a name is only ever shown when a NON-BLANK actor
        // uid resolved to it -- which makes the desktop's ordering part of this
        // client's contract, so a revert over there has to be red over here.
        val js = File(suiteRoot, "products/retail/frontend/subsystem-retail.js")
        assumeTrue("subsystem-retail.js not reachable from this run context", js.exists())
        val state = js.readText().substringAfter("_attributionState(row) {").substringBefore("},")

        // Stated as the ORDERING rather than as an exact line, so rewording the
        // desktop does not break this while reversing it still does: the
        // unattributed verdict is reached before the named one...
        assertThat(state).contains("'unattributed'")
        assertThat(state).contains("'named'")
        assertThat(state.indexOf("'unattributed'")).isLessThan(state.indexOf("'named'"))

        // ...it is the UID that decides it, and no name lookup happens above
        // that decision.
        val beforeTheVerdict = state.substringBefore("'unattributed'")
        assertThat(beforeTheVerdict).contains("actor_user_uid")
        assertThat(beforeTheVerdict).doesNotContain("_identityOf")

        // Blank-is-absent on the uid too, the same rule isNullOrBlank() applies
        // here -- a uid made of spaces is not a recorded actor on either
        // client, and `attributionOf("   ", …)` above pins this side of it.
        assertThat(beforeTheVerdict).contains(".trim()")
    }

    @Test
    fun a_named_row_shows_the_email_the_employees_screen_shows() {
        // EmployeesScreen renders the email as the primary identifier and the
        // server-assigned EMP-000n underneath. The owner thinks about staff in
        // those terms, so the report has to agree with the screen they manage
        // staff on -- and fall back to the EMP id when the email is missing
        // rather than dropping to "unattributed".
        val named = EmployeeSales(actor_user_uid = "u1", employee_id = "EMP-0002", email = "sam@shop.test")
        assertThat(rowAttribution(named)).isEqualTo(Attribution.NAMED)
        assertThat(attributedName(named.employee_id, named.email)).isEqualTo("sam@shop.test")

        val idOnly = EmployeeSales(actor_user_uid = "u1", employee_id = "EMP-0002")
        assertThat(rowAttribution(idOnly)).isEqualTo(Attribution.NAMED)
        assertThat(attributedName(idOnly.employee_id, idOnly.email)).isEqualTo("EMP-0002")

        // Whitespace is not an identity.
        assertThat(attributedName("  ", " ")).isNull()
    }

    @Test
    fun a_sale_that_predates_attribution_says_so_rather_than_showing_nothing() {
        // The detail sheet has to state the absence. Rendering no line at all
        // is indistinguishable from a screen that forgot to show it, and the
        // one thing an owner must be able to tell is whether the shop has
        // attribution from this date onward.
        assertThat(saleAttribution(Sale(id = 1))).isEqualTo(Attribution.NOT_RECORDED)
        assertThat(saleAttribution(null)).isEqualTo(Attribution.NOT_RECORDED)
        assertThat(saleAttribution(Sale(id = 1, actor_user_uid = "u9")))
            .isEqualTo(Attribution.ACCOUNT_GONE)
        assertThat(saleAttribution(Sale(id = 1, actor_user_uid = "u9", actor_email = "sam@shop.test")))
            .isEqualTo(Attribution.NAMED)
        // ...and the sheet actually asks.
        assertThat(codeOnly(extraScreens)).contains("saleAttribution(")
    }

    @Test
    fun the_legacy_free_text_cashier_column_is_not_a_field_on_the_client_model() {
        // `sales.cashier` is not a name. Its schema default is the literal
        // 'POS' and create_sale writes session['mt_user_id'] into it, so it
        // holds a placeholder or an opaque account id -- never something to
        // print next to "Rung by". v13 keeps the column precisely because it
        // is the only surviving evidence of what the shop BELIEVED, and
        // deliberately never reads it to guess an actor. A field here would be
        // an invitation to render it.
        val fields = Sale::class.java.declaredFields.map { it.name }
        assertThat(fields).doesNotContain("cashier")
        assertThat(fields).containsAtLeast("actor_user_uid", "actor_employee_id", "actor_email")
    }

    @Test
    fun a_refund_only_employee_reports_negative_takings_and_is_not_painted_as_earnings() {
        // metrics.revenue_by_employee assigns a refund to whoever PROCESSED
        // it, not to whoever rang the original sale, and its docstring calls
        // the resulting negative figure correct and explicitly not to be
        // "fixed". So the client must not fix it either -- but it must not
        // print it in the same green as takings, because "-$40.00" in the
        // earnings colour reads as money earned at a glance.
        assertThat(isMoneyOut(-40.0)).isTrue()
        assertThat(isMoneyOut(0.0)).isFalse()
        assertThat(isMoneyOut(0.01)).isFalse()
        assertThat(codeOnly(screen)).contains("isMoneyOut(row.revenue)")
    }

    @Test
    fun a_latin_identity_is_bidi_isolated_before_it_lands_in_an_arabic_sentence() {
        // "نفّذها sam@shop.test" is one paragraph with an RTL base direction
        // and a strongly-LTR run inside it. Without an isolate, the Unicode
        // bidi algorithm resolves the neighbouring punctuation and digits
        // against the LTR run, so a trailing '.' or an EMP-0002 lands on the
        // wrong side of the name -- the email visibly reorders, and on a
        // display-only string the user has no way to tell it apart from
        // corrupted data. FSI/PDI (U+2068/U+2069) scopes the run without
        // asserting a direction for it, which is what "first strong isolate"
        // is for: the identity itself decides, the sentence around it does
        // not have to care.
        assertThat(bidiIsolate("sam@shop.test")).isEqualTo("⁨sam@shop.test⁩")
        // Nothing to isolate stays nothing -- an empty isolate would be two
        // invisible characters rendered where a name should be.
        assertThat(bidiIsolate("")).isEmpty()
        assertThat(codeOnly(screen)).contains("bidiIsolate(")
        assertThat(codeOnly(extraScreens)).contains("bidiIsolate(")
    }

    // ── Arabic coverage ──────────────────────────────────────────────────────

    @Test
    fun every_translated_string_on_the_screen_has_an_arabic_entry() {
        // This product ships Arabic and is RTL. tr() falls back to the English
        // key when a translation is missing, which degrades gracefully and
        // therefore FAILS SILENTLY -- an owner in Amman sees an English
        // sentence and nothing anywhere reports a problem. This is the only
        // thing that reports it.
        val used = trKeys(codeOnly(screen)).toSet()
        assertThat(used).isNotEmpty()
        assertThat(used.filterNot { it in catalog }.sorted()).isEmpty()
    }

    @Test
    fun the_screens_this_change_touches_have_no_untranslated_strings() {
        // Scoped to the files this feature edits, not the whole app: a guard
        // that fails on somebody else's screen gets muted rather than fixed.
        // RetailExtraScreens.kt carries both the Reports entry point and the
        // sale detail sheet, and AppRoot.kt carries the nav title -- so both
        // are now this feature's problem.
        for (file in listOf(codeOnly(extraScreens), codeOnly(appRoot))) {
            val used = trKeys(file).toSet()
            assertThat(used).isNotEmpty()
            assertThat(used.filterNot { it in catalog }.sorted()).isEmpty()
        }
    }

    @Test
    fun the_navigation_label_and_the_reports_entry_are_translated() {
        assertThat(catalog).containsAtLeast(
            "By Employee",
            "Takings and transactions per employee",
            "Not attributed",
            "Account removed",
        )
    }
}
