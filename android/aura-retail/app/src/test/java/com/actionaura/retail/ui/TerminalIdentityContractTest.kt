package com.actionaura.retail.ui

import com.actionaura.retail.net.DeviceRow
import com.actionaura.retail.net.MyDeviceResponse
import com.actionaura.retail.net.TerminalIdentity
import com.google.common.truth.Truth.assertThat
import com.google.gson.Gson
import kotlinx.coroutines.runBlocking
import okhttp3.MediaType.Companion.toMediaTypeOrNull
import okhttp3.ResponseBody.Companion.toResponseBody
import org.junit.Assume.assumeTrue
import org.junit.Test
import retrofit2.HttpException
import retrofit2.Response
import java.io.File
import java.io.IOException

/**
 * `sales.terminal_id` answers "which till rang this?", and this app IS a till.
 *
 * ── What was actually broken ─────────────────────────────────────────────────
 * The backend stamps every write through `retail_api.py::_stamp()`, which calls
 * `database/schema.py::local_terminal_id()`. That function deliberately uses
 * `device_context.peek_local_device_uuid()` -- the PEEK variant, which never
 * CREATES the identity file -- because "stamping a row is a bookkeeping
 * question, not a reason to manufacture an install identity as a side effect".
 * The only thing in the whole product that creates
 * `<AURA_APP_DATA>/device/local_device.json` is
 * `device_context.local_device_uuid()`, reached exclusively from
 * `device_routes.py`'s handlers under the `/api/devices` prefix.
 *
 * (Spelled as a prefix rather than with a trailing glob star, here and below,
 * because Kotlin block comments NEST: a slash immediately followed by a star
 * inside this KDoc opens a SECOND comment, and the terminator below then only
 * closes that one. This file shipped with two such stars and one terminator,
 * so the whole class -- all eleven assertions -- was a single unterminated
 * comment and the module did not compile. RetailSession.kt and
 * TerminalIdentity.kt carry the same note for the same reason.)
 *
 * The desktop shell calls `GET /api/devices/me` once at init()
 * (app-shell.js:675) and so has a terminal id from its first launch. This
 * client called it NOWHERE -- a grep for "api/devices" across
 * app/src/main returned nothing -- so every sale, return and stock adjustment
 * ever rung on a handset carried `terminal_id` NULL, permanently, and the
 * Till cell rendered "Not recorded" for all of them.
 *
 * ── Proven end to end, not by unit test ──────────────────────────────────────
 * A JVM unit test cannot start Chaquopy, so the mechanism was proven against
 * the REAL Flask backend (the same app.py the APK embeds) over its real HTTP
 * surface, from a genuinely fresh AURA_APP_DATA:
 *
 *   PHASE A -- exactly what this client did (nothing under /api/devices called):
 *     inventory_movement id=1 terminal_id=None
 *     sale id=1 number=SALE-1333184-eabec5df terminal_id=None
 *     local_device.json present? False
 *
 *   PHASE B -- one GET /api/devices/me first, then the same two writes:
 *     local_device.json present? True
 *     inventory_movement id=3 terminal_id='74a377e1-cbfd-4d0d-9ca3-36a61c3f59a3'
 *     sale id=2 number=SALE-1333185-eabec5df terminal_id='74a377e1-…'
 *     ...which is byte-identical to the `devices.id` that same call returned.
 *
 * One GET is necessary and sufficient. What this file pins is that the client
 * makes it, that it makes it on every path that reaches the till, and that
 * failing to make it can never take the app down with it.
 */
class TerminalIdentityContractTest {

    private val moduleRoot = File(".")
    // Gradle unit tests run with the module directory (android/aura-retail/app)
    // as the working directory -- see ReadinessContractTest.
    private val suiteRoot = File("../../..")
    private val gson = Gson()

    private fun source(relative: String): String {
        val file = File(moduleRoot, relative)
        assumeTrue("$relative not reachable from this run context", file.exists())
        return file.readText()
    }

    private val appRoot get() = source("src/main/java/com/actionaura/retail/ui/AppRoot.kt")
    private val api get() = source("src/main/java/com/actionaura/retail/net/AuraApi.kt")

    private fun mainSources(): List<File> =
        File(moduleRoot, "src/main/java/com/actionaura/retail")
            .walkTopDown().filter { it.isFile && it.extension == "kt" }.toList()

    // ── The route exists on this client at all ───────────────────────────────

    @Test
    fun the_client_declares_the_route_that_establishes_this_devices_terminal_id() {
        // The whole finding in one assertion: before this, "api/devices"
        // appeared nowhere under src/main.
        assertThat(codeOnly(api)).contains("api/devices/me")
    }

    @Test
    fun the_backend_still_only_creates_the_terminal_identity_through_that_route() {
        // Read out of the Python that DECIDES it, not restated here. If
        // local_terminal_id() ever stopped PEEKing -- i.e. started creating the
        // identity itself -- this client's call would become redundant rather
        // than load-bearing, and somebody should be told rather than left
        // guessing why it is here.
        val schema = File(suiteRoot, "products/retail/backend/database/schema.py")
        assumeTrue("schema.py not reachable from this run context", schema.exists())
        val text = schema.readText()
        assertThat(text).contains("def local_terminal_id():")
        assertThat(text).contains("return peek_local_device_uuid()")

        // ...and the creating half really is only reachable from the device
        // routes, which is why calling one of them is the fix.
        val ctx = File(suiteRoot, "commercial_runtime/identity/device_context.py")
        assumeTrue("device_context.py not reachable from this run context", ctx.exists())
        assertThat(ctx.readText()).contains("def local_device_uuid()")

        val routes = File(suiteRoot, "commercial_runtime/identity/device_routes.py")
        assumeTrue("device_routes.py not reachable from this run context", routes.exists())
        assertThat(routes.readText()).contains("@device_bp.route('/me', methods=['GET'])")
    }

    // ── The client actually calls it, on every path to the till ──────────────

    @Test
    fun the_session_adoption_path_establishes_the_terminal_identity() {
        val code = codeOnly(appRoot)
        assertThat(code).contains("TerminalIdentity.establish")
        // Inside adoptSession(), not bolted onto one screen: that is the single
        // funnel every route to Phase.READY goes through below.
        val adopt = code.substringAfter("private suspend fun adoptSession(")
        assertThat(adopt.substringBefore("private ")).contains("TerminalIdentity.establish")
    }

    @Test
    fun every_path_that_reaches_the_main_shell_goes_through_that_funnel() {
        // Three ways in, and until now only the cold-start one resolved a
        // session at all: a fresh install that completes SETUP, or an account
        // that LOGs IN, went straight to Phase.READY. Both of those are
        // precisely the moments a brand-new till starts ringing sales, so
        // leaving them out would have fixed the case that needed it least.
        val code = codeOnly(appRoot)
        assertThat(code).contains("Phase.SETUP -> SetupScreen(onDone = { scope.launch { adoptSession()")
        assertThat(code).contains("Phase.LOGIN -> LoginScreen(onLoggedIn = { scope.launch { adoptSession()")
        assertThat(code).contains("private suspend fun phaseAfterActivationGate")
        assertThat(code.substringAfter("private suspend fun phaseAfterActivationGate"))
            .contains("adoptSession()")
    }

    @Test
    fun nothing_else_in_the_app_reaches_for_the_device_route_on_its_own() {
        // One caller, one place to reason about. A second ad-hoc call site
        // would be a second answer to "when does this till get its identity".
        val callSites = mainSources().sumOf { f ->
            Regex("""myDevice\(""").findAll(codeOnly(f.readText())).count()
        }
        // One declaration in AuraApi.kt + one call in TerminalIdentity.kt.
        assertThat(callSites).isEqualTo(2)
    }

    // ── The behaviour itself ─────────────────────────────────────────────────

    @Test
    fun establishing_returns_the_device_id_the_backend_will_stamp_rows_with() {
        val id = "74a377e1-cbfd-4d0d-9ca3-36a61c3f59a3"
        val resolved = runBlocking {
            TerminalIdentity.establish {
                MyDeviceResponse(success = true, device = DeviceRow(id = id))
            }
        }
        assertThat(resolved).isEqualTo(id)
        assertThat(TerminalIdentity.lastResolvedId).isEqualTo(id)
    }

    @Test
    fun the_real_response_body_parses_into_that_device_id() {
        // The literal body GET /api/devices/me returned in the end-to-end run
        // quoted in this class's KDoc -- so the model is pinned against a
        // response the server really sent, not one this test invented.
        val body = """
            {"can_claim_admin": true,
             "device": {"company_id": "eabec5df-142f-4113-a1f2-10ea649bb1f6",
                        "device_fingerprint": null, "device_label": null,
                        "first_seen_at": "2026-08-21T17:17:23.369163+00:00",
                        "id": "74a377e1-cbfd-4d0d-9ca3-36a61c3f59a3",
                        "is_admin_device": false, "last_seen_at": "2026-08-21T17:17:23.369163+00:00",
                        "owner_installation_id": null, "platform": null, "status": "active"},
             "success": true}
        """.trimIndent()
        val parsed = gson.fromJson(body, MyDeviceResponse::class.java)
        assertThat(parsed.success).isTrue()
        assertThat(parsed.device?.id).isEqualTo("74a377e1-cbfd-4d0d-9ca3-36a61c3f59a3")
    }

    /**
     * Collects what `establish` would have written to logcat.
     *
     * `android.util.Log` is an Android platform STUB on the unit-test
     * classpath: every method throws "not mocked". That is why
     * [TerminalIdentity.establish] takes its warning sink as a parameter, and
     * it is also why these two tests failed the very first time they were ever
     * executed -- this whole file had been one unterminated block comment since
     * the day it was written, so nobody had run them. Recording the warnings
     * rather than discarding them turns "returned null" into "returned null AND
     * said why", which is the assertion actually worth having: a silent null is
     * indistinguishable from a device that simply has no identity yet.
     */
    private class Warnings : (String, Throwable?) -> Unit {
        val recorded = mutableListOf<Pair<String, Throwable?>>()
        override fun invoke(message: String, cause: Throwable?) {
            recorded += message to cause
        }
    }

    @Test
    fun a_failure_to_establish_is_swallowed_and_never_reaches_the_boot_sequence() {
        // Bookkeeping must never cost a sale. `_stamp()`'s own Python twin says
        // the same thing in as many words -- "an unattributed row is
        // recoverable; a refused sale is not" -- so a 401/403/dead socket here
        // has to degrade to exactly the behaviour that shipped before this call
        // existed, not to a startup error screen.
        val warnings = Warnings()

        assertThat(runBlocking {
            TerminalIdentity.establish(warnings) { throw IOException("no route to host") }
        }).isNull()

        assertThat(runBlocking {
            TerminalIdentity.establish(warnings) {
                throw HttpException(
                    Response.error<Any>(401, "".toResponseBody("application/json".toMediaTypeOrNull())),
                )
            }
        }).isNull()

        // Swallowed, not hidden. "Why is terminal_id null on this device" has
        // exactly one answer and this is the only place it gets recorded, so a
        // degradation that logged nothing would be the same outage with the
        // evidence removed.
        assertThat(warnings.recorded.map { it.second?.javaClass?.simpleName })
            .containsExactly("IOException", "HttpException")
    }

    @Test
    fun a_body_with_no_usable_device_id_is_not_treated_as_an_identity() {
        // A refusal that still answers 200 (`{"success": false}`), a null
        // device, and a blank id all mean the same thing: this device has no
        // terminal identity yet. Recording "" as one would put an empty string
        // where a uuid belongs on the next screen that reads it.
        val warnings = Warnings()

        assertThat(runBlocking {
            TerminalIdentity.establish(warnings) { MyDeviceResponse(success = false, device = null) }
        }).isNull()
        assertThat(runBlocking {
            TerminalIdentity.establish(warnings) {
                MyDeviceResponse(success = true, device = DeviceRow(id = "   "))
            }
        }).isNull()

        assertThat(warnings.recorded).hasSize(2)
        warnings.recorded.forEach { (message, cause) ->
            // A 200 with no id is not a failure to reach the server, so it
            // carries no throwable -- and it still has to say what it costs.
            assertThat(message).contains("terminal_id NULL")
            assertThat(cause).isNull()
        }
    }

    @Test
    fun the_default_warning_sink_really_reaches_logcat() {
        // The seam those two tests use is the one risk this refactor adds: if
        // the DEFAULT quietly became a no-op, every assertion above would stay
        // green while the only recorded answer to "why is terminal_id null on
        // this device" stopped being recorded on real handsets.
        //
        // Proven by CALLING it rather than by reading the source. On this
        // classpath android.util.Log is a stub that throws, so the throw IS the
        // evidence that the default reaches it. (Which also means this test
        // fails if anyone switches testOptions.unitTests.returnDefaultValues on
        // -- correctly: that switch would neutralise every Android-API
        // assertion in this whole source set, not just this one.)
        var escaped: Throwable? = null
        try {
            runBlocking { TerminalIdentity.establish { throw IOException("probe") } }
        } catch (e: RuntimeException) {
            escaped = e
        }
        assertThat(escaped).isNotNull()
        assertThat(escaped).hasMessageThat().contains("android.util.Log")

        // ...and the sink it defaults to is the logcat one, at the WARN level
        // this file's KDoc promises. Read out of the source because the
        // assertion above proves only that SOMETHING reached Log.
        assertThat(codeOnly(source("src/main/java/com/actionaura/retail/net/TerminalIdentity.kt")))
            .contains("Log.w(TAG, message, cause)")
    }

    @Test
    fun a_cancelled_coroutine_still_cancels() {
        // The one Throwable that must NOT be swallowed: structured concurrency
        // depends on CancellationException propagating, and a `catch
        // (e: Exception)` here would quietly turn a cancelled boot into a
        // successful one that never ran.
        var cancelled = false
        try {
            runBlocking {
                TerminalIdentity.establish {
                    throw kotlin.coroutines.cancellation.CancellationException("boot cancelled")
                }
            }
        } catch (e: kotlin.coroutines.cancellation.CancellationException) {
            cancelled = true
        }
        assertThat(cancelled).isTrue()
    }
}
