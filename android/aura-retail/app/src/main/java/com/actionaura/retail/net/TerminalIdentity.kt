package com.actionaura.retail.net

import android.util.Log
import kotlin.coroutines.cancellation.CancellationException

/**
 * Makes this handset a TILL the backend can name, by asking the embedded server
 * for this device's own registry row exactly once per resolved session.
 *
 * ── Why this file exists ─────────────────────────────────────────────────────
 * `sales.terminal_id` answers "which till rang this?". Retail schema v13 added
 * it, `retail_api.py::_stamp()` writes it on every sale, return, stock
 * adjustment, cash session and cash movement, and it comes from
 * `database/schema.py::local_terminal_id()`:
 *
 *     return peek_local_device_uuid()
 *
 * PEEK, not create -- deliberately, and its docstring says why: "Stamping a row
 * is a bookkeeping question, not a reason to manufacture an install identity as
 * a side effect." The only thing that creates
 * `<AURA_APP_DATA>/device/local_device.json` is
 * `device_context.local_device_uuid()`, reached only from `device_routes.py`'s
 * handlers under `/api/devices`.
 *
 * (Spelled as a prefix, not a glob: Kotlin block comments NEST, so a literal
 * slash-star inside this KDoc opens a second comment and swallows the rest of
 * the file -- see RetailSession.kt's CAP_REPORTS comment, which paid one
 * compile to rediscover exactly this.)
 *
 * The desktop shell calls `GET /api/devices/me` once at init()
 * (app-shell.js:675) and has had a terminal id since its first launch. This
 * client called it NOWHERE. Every sale ever rung on a phone therefore carried
 * `terminal_id` NULL, permanently, and the Till cell rendered "Not recorded"
 * for all of them -- on the one device in the product that unambiguously IS a
 * till.
 *
 * Proven against the real backend from a fresh AURA_APP_DATA: without the call,
 * `sale.terminal_id=None` and `local_device.json` never appears; with one GET
 * first, `sale.terminal_id` equals the `devices.id` that GET returned, byte for
 * byte. TerminalIdentityContractTest carries the full transcript.
 *
 * ── Why it is shaped like this ───────────────────────────────────────────────
 * The seam is a `suspend () -> MyDeviceResponse` rather than the whole
 * [AuraApi], so this is unit-testable without stubbing sixty unrelated
 * endpoints and without a device, a server or MockWebServer -- none of which
 * exist in this module's test environment.
 *
 * The route is idempotent server-side (`resolve_local_device` upserts and
 * check-ins, and NEVER touches `is_admin_device` -- that is the invariant the
 * 2026-08-20 audit-log fix rests on), so calling it again on every session
 * resolution is a check-in, not a mutation. Nothing here claims, transfers or
 * reads any privilege; it exists to make the identity file exist.
 */
object TerminalIdentity {

    private const val TAG = "TerminalIdentity"

    /**
     * The last id the server reported for this device, or null if it has never
     * successfully resolved. Observability only -- the authority is the
     * backend's own `local_device.json`, and nothing may gate on this.
     */
    @Volatile
    var lastResolvedId: String? = null
        private set

    /**
     * Where the two diagnostics below go. Defaults to logcat, which is the only
     * place they belong on a real handset.
     *
     * A SEAM, not indirection for its own sake. `android.util.Log` is an
     * Android PLATFORM STUB on the unit-test classpath -- every method throws
     * `RuntimeException("Method w in android.util.Log not mocked")` -- so a
     * class that calls it directly cannot be unit-tested at all unless
     * `testOptions.unitTests.returnDefaultValues` is switched on, and that
     * switch is module-wide: it would silently neutralise every OTHER
     * accidental Android-API call in every other test in this source set at the
     * same time. SyncRelayClient avoids the problem by touching no Android API;
     * this class genuinely wants logcat on a device, so it takes the sink as a
     * parameter instead. The same seam lets a test assert that the warning was
     * EMITTED rather than merely that the call returned null -- which is the
     * difference between checking the outcome and checking that the check ran.
     *
     * (Found by running these tests for the first time. The contract test file
     * had been one unterminated block comment since the day it was written, so
     * its ten assertions had never executed -- see
     * CompiledTestSuiteRollCallTest, which now makes that state loud.)
     */
    private val logcat: (String, Throwable?) -> Unit = { message, cause -> Log.w(TAG, message, cause) }

    /**
     * Resolve this device's registry row, which establishes the terminal
     * identity as a side effect. Returns the device id, or null when it could
     * not be established.
     *
     * NEVER throws for a network, auth or server failure. Attribution is
     * bookkeeping and the app is a shop: `_stamp()`'s own Python twin refuses
     * to raise for exactly this reason -- "an unattributed row is recoverable;
     * a refused sale is not" -- and this call sits on the boot path, where an
     * escaping exception would put a startup error screen in front of a user
     * whose only problem is that the identity file is one request late.
     *
     * [CancellationException] is the one Throwable that is rethrown. Swallowing
     * it would break structured concurrency and turn a cancelled boot into one
     * that silently reports success.
     *
     * [warn] is declared before [fetch] so the production call site stays
     * `establish { api.myDevice() }` -- a trailing lambda binds to the last
     * parameter, and the sink is the one a caller almost never supplies.
     */
    suspend fun establish(
        warn: (String, Throwable?) -> Unit = logcat,
        fetch: suspend () -> MyDeviceResponse,
    ): String? = try {
        val id = fetch().device?.id?.trim()?.takeIf { it.isNotEmpty() }
        if (id == null) {
            // A 200 that resolves no device is not an identity. Logged rather
            // than surfaced: the user cannot act on it, but whoever is holding
            // the handset with logcat attached can.
            warn("Device row resolved with no usable id; rows written now carry terminal_id NULL.", null)
        }
        lastResolvedId = id
        id
    } catch (e: CancellationException) {
        throw e
    } catch (e: Exception) {
        // Expected and harmless before login (401) and on a session with no
        // tenant context (403). Everything else is worth a line in logcat,
        // because "why is terminal_id null on this device" has exactly one
        // answer and this is where it gets recorded.
        warn("Could not establish this device's terminal identity: ${e.javaClass.simpleName}", e)
        null
    }
}
