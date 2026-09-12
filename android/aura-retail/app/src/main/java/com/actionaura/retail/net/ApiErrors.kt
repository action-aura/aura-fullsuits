package com.actionaura.retail.net

import com.actionaura.retail.ui.i18n.tr
import org.json.JSONObject
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
    is HttpException -> when (e.code()) {
        // Licensing/subscription guard. Prefix makes unmistakably clear the
        // block is the subscription, not the network; the server's own
        // message (flask_guard.py's "message" field) is appended so the
        // user sees exactly what the backend refused and why.
        403 -> tr("Blocked by your subscription/license:") + " " +
            tr(serverMessageOf(e) ?: "This action is not available in the current licensing state.")
        // mt_login_required (commercial_runtime/identity/mt_auth.py):
        // session cookie missing, expired, or revoked.
        401 -> tr("Your session has expired. Please log in again.")
        // Any other HTTP error: surface the server's message when it sent
        // one (e.g. the AI route's 503 "AI assistant is temporarily
        // unavailable."), otherwise at least name the status code instead
        // of pretending the network is down.
        else -> serverMessageOf(e)?.let { tr(it) }
            ?: (tr("Server error") + " (HTTP ${e.code()})")
    }
    // Real connectivity failure (unreachable, refused, timed out): the one
    // case where the historical blanket message was actually correct.
    is IOException -> tr("Couldn't reach the server")
    else -> tr("Unexpected error") + " (${e.javaClass.simpleName})"
}

/**
 * Best-effort extraction of the server's own human-readable error text.
 * The backend uses several error envelopes: {"status":"error","message":...}
 * (flask_guard + most retail routes), {"success":false,"error":...} (the AI
 * proxy route), and {"error":...,"code":...} (mt_auth's 401s) -- so check
 * "message" first, then "error". Any parse failure just means "no usable
 * server text"; an error path must never itself throw.
 */
private fun serverMessageOf(e: HttpException): String? = try {
    val raw = e.response()?.errorBody()?.string()
    if (raw.isNullOrBlank()) null
    else JSONObject(raw).let { body ->
        sequenceOf("message", "error")
            .map { body.optString(it, "") }
            .firstOrNull { it.isNotBlank() }
    }
} catch (_: Exception) { null }
