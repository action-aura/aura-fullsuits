package com.actionaura.retail.net

import com.actionaura.retail.ui.i18n.tr
import com.google.gson.JsonParser
import retrofit2.HttpException
import java.io.IOException

/**
 * The single Throwable -> user-facing-message mapping for every screen's
 * "call failed" catch block.
 *
 * Why this exists: every screen used to collapse ALL failures into
 * tr("Couldn't reach the server"). But Retrofit suspend calls throw
 * retrofit2.HttpException for any non-2xx response, so a licensing 403 from
 * the capability guard (commercial_runtime/licensing_contracts/flask_guard.py,
 * body {"status":"error","reason_code":...,"message":...}) was reported as a
 * connectivity problem -- sending a shop owner whose license is RESTRICTED
 * off to debug their wifi when their subscription is the actual problem.
 * This helper keeps genuine connectivity failures (IOException) reading as
 * connectivity, and gives everything else an honest, specific message. It
 * never weakens enforcement: the action stays blocked either way, only the
 * explanation changes.
 *
 * Lives in `net` (not `ui`) because the envelope shapes it decodes are API
 * contracts of the embedded server, and this is where every screen already
 * imports its request/response models from.
 *
 * Every string returned here goes through tr(...) -- including the server's
 * own message text, so the fixed guard sentence renders in Arabic too. An
 * unknown server message falls back to its English original, which is still
 * more honest than a wrong "server unreachable".
 */
fun apiErrorMessage(e: Throwable): String = when (e) {
    is HttpException -> {
        // An HTTP error body is ONE-SHOT (okhttp's ResponseBody.string()
        // consumes it), so parse it exactly once and hand the parts around.
        val body = parseErrorBody(e)
        when (e.code()) {
            // TWO different servers-side refusals share HTTP 403 and used to
            // both read "Blocked by your subscription/license":
            //   * the LICENCE guard (licensing_contracts/flask_guard.py) answers
            //     {"status":"error","reason_code":"LICENSE_INACTIVE"|
            //      "CAPABILITY_NOT_ENTITLED","message":...};
            //   * the PERMISSION guard (identity/mt_auth.py
            //     _capability_denied_response) answers
            //     {"status":"error","error":...,"message":...,"code":403} and
            //     carries NO reason_code.
            // The discriminator is a non-blank string `reason_code`. Before this,
            // a cashier refused a manager-only action was told to check their
            // subscription, which is exactly the wrong thing to go and fix.
            403 ->
                if (body.reasonCode != null) {
                    // Licensing/subscription guard. Prefix makes unmistakably
                    // clear the block is the subscription, not the network; the
                    // server's own message is appended so the user sees exactly
                    // what the backend refused and why.
                    tr("Blocked by your subscription/license:") + " " +
                        tr(body.message ?: "This action is not available in the current licensing state.")
                } else {
                    tr("You don't have permission to do this.")
                }
            // mt_login_required (commercial_runtime/identity/mt_auth.py):
            // session cookie missing, expired, or revoked.
            401 -> tr("Your session has expired. Please log in again.")
            // ANDROID-1007: a 409 carrying one of the purchase-order / transfer
            // refusal codes gets a plain sentence instead of the server's
            // developer-facing one; any other 409 falls through to the
            // server's own message exactly as before.
            409 -> conflictMessageFor(body.code)
                ?: body.message?.let { tr(it) }
                ?: (tr("Server error") + " (HTTP 409)")
            // Any other HTTP error: surface the server's message when it sent
            // one (e.g. the AI route's 503 "AI assistant is temporarily
            // unavailable."), otherwise at least name the status code instead
            // of pretending the network is down.
            else -> body.message?.let { tr(it) }
                ?: (tr("Server error") + " (HTTP ${e.code()})")
        }
    }
    // Real connectivity failure (unreachable, refused, timed out): the one
    // case where the historical blanket message was actually correct.
    is IOException -> tr("Couldn't reach the server")
    else -> tr("Unexpected error") + " (${e.javaClass.simpleName})"
}

/**
 * [apiErrorMessage], specialised for the ONE call where a 401 does NOT mean
 * "your session expired": `POST api/auth/login` itself.
 *
 * mt_auth answers a wrong password with 401 and body
 * `{"error":"Invalid email or password."}` (commercial_runtime/identity/
 * mt_auth.py). [apiErrorMessage]'s 401 branch assumes an ALREADY-
 * authenticated route whose session cookie went stale or was revoked --
 * exactly backwards here, since this catch runs on the very call that is
 * trying to establish that cookie in the first place.
 *
 * PROVEN on a real device: the embedded backend was healthy (`GET
 * /api/health` -> 200) and a direct `POST /api/auth/login` with the typed
 * credentials returned `401 {"error":"Invalid email or password."}`, while
 * LoginScreen's old bare `catch (e: Exception)` mapped that -- like every
 * other failure -- to "Couldn't reach the server. Try again.", sending a
 * cashier who mistyped a password off to check the wifi.
 *
 * Delegates every other case to [apiErrorMessage] rather than duplicating
 * it, so IOException, 403, any other HTTP status and the unexpected-
 * exception fallback can never drift from what every other screen shows --
 * this can only ever differ from it on the one code it exists to override.
 */
fun loginErrorMessage(e: Throwable): String =
    if (e is HttpException && e.code() == 401) {
        parseErrorBody(e).message?.let { tr(it) } ?: tr("Invalid email or password.")
    } else {
        apiErrorMessage(e)
    }

/**
 * Best-effort extraction of the server's own human-readable error text.
 * The backend uses several error envelopes: {"status":"error","message":...}
 * (flask_guard + most retail routes), {"success":false,"error":...} (the AI
 * proxy route), and {"error":...,"code":...} (mt_auth's 401s) -- so check
 * "message" first, then "error". Any parse failure just means "no usable
 * server text"; an error path must never itself throw.
 *
 * com.google.gson.JsonParser, NOT org.json.JSONObject: the latter is an
 * Android-platform-stubbed class whose methods throw "not mocked" under
 * plain JUnit (same trap build.gradle's Gson dependency comment and
 * ReceiptPayloadContractTest.kt's android.util.Base64 comment already
 * record for this module) -- `catch (_: Exception)` below silently
 * swallowed that throw and always returned null, so this function has never
 * actually been exercised by a JVM unit test, only ever by a real device.
 * Gson is a real pure-JVM library already a direct dependency (see
 * build.gradle's own comment on DeviceIdentity.kt for the identical fix),
 * so this is the same parser RetailExtraScreens.kt's pairing code already
 * uses for the same reason.
 */
private data class ErrorBody(val message: String?, val reasonCode: String?, val code: String? = null)

private val NO_ERROR_BODY = ErrorBody(message = null, reasonCode = null)

/**
 * ANDROID-1007: friendly, translated text for the 409 `code` values the
 * purchase-order receive/close and stock-transfer routes answer with. Null for
 * a code this table does not know, so the caller falls back to the server's
 * own message. A native Arabic speaker should confirm the Arabic wording in
 * ui/i18n/Strings.kt.
 */
internal fun conflictMessageFor(code: String?): String? = when (code) {
    "OVER_RECEIPT" -> tr("You cannot receive more than is still on order.")
    "PO_LINES_NOT_ARRIVED", "PO_RECEIPTS_NOT_ARRIVED", "PO_BOOKS_NOT_ARRIVED",
    "TRANSFER_LINES_NOT_ARRIVED", "TRANSFER_SEND_NOT_ARRIVED", "TRANSFER_BOOKS_NOT_ARRIVED" ->
        tr("This hasn't finished syncing from your other device yet. Try again in a moment.")
    "CLOSE_SHORT_NOT_PARTIAL" -> tr("Only an order that is partly received can be closed short.")
    "CLOSE_SHORT_NEEDS_SUPPLIER" -> tr("This order needs a supplier before it can be closed short.")
    else -> null
}

private fun parseErrorBody(e: HttpException): ErrorBody = try {
    val raw = e.response()?.errorBody()?.string()
    if (raw.isNullOrBlank()) NO_ERROR_BODY
    else JsonParser.parseString(raw).asJsonObject.let { body ->
        ErrorBody(
            message = sequenceOf("message", "error")
                .mapNotNull { key -> body.get(key)?.takeUnless { it.isJsonNull }?.asString }
                .firstOrNull { it.isNotBlank() },
            // A non-blank STRING only: a numeric or null reason_code is not the
            // licence guard's shape and must not flip a permission refusal into
            // a subscription one.
            reasonCode = body.get("reason_code")
                ?.takeIf { it.isJsonPrimitive && it.asJsonPrimitive.isString }
                ?.asString?.takeIf { it.isNotBlank() },
            // The 409 refusals carry a STRING `code`; mt_auth's 403 carries the
            // number 403 there, which must not be mistaken for one.
            code = body.get("code")
                ?.takeIf { it.isJsonPrimitive && it.asJsonPrimitive.isString }
                ?.asString?.takeIf { it.isNotBlank() },
        )
    }
} catch (_: Exception) { NO_ERROR_BODY }
