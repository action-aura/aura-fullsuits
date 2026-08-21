package com.actionaura.retail.ui

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import com.actionaura.retail.net.User

/**
 * Client-side admin-state for UI-level gating (Wave 1A, Part G). Usability
 * only -- the backend independently enforces admin-only access on the
 * backup/restore endpoints (commercial_runtime/backup/routes.py) regardless
 * of what this UI shows. Never assume Admin as a fallback when role data
 * is missing or unrecognized.
 */
fun isAdminUser(user: User?): Boolean = user?.role == "admin"

/**
 * "See, send or generate the shop's numbers" -- the per-user capability every
 * route under `/api/sub/retail/reports/` is gated on server-side
 * (retail_api.py's `@mt_require_capability(CAP_REPORTS)`).
 *
 * (Spelled without a trailing wildcard on purpose: Kotlin block comments
 * NEST, so a literal slash-star inside this KDoc opens a second comment and
 * swallows the rest of the file. Cost one compile to rediscover.)
 *
 * Spelled here as a constant rather than inline at each call site so the
 * string exists in exactly one place on this client, and pinned against
 * `commercial_runtime/identity/user_accounts.py::CAP_REPORTS` by
 * EmployeeSalesWiringContractTest. A capability code that has drifted from the
 * server's does not fail loudly: it silently hides a screen from everybody, or
 * shows it to everybody.
 */
const val CAP_REPORTS = "retail.reports"

/**
 * True if `capabilities` grants `code`.
 *
 * RENDERING ADVICE ONLY, and a deliberate mirror of app-shell.js's
 * `hasCapability()` on the desktop shell -- including its fail-open default,
 * which is the part that looks like a bug and is not:
 *
 *   `caps == null` covers two states this function treats identically --
 *   "/api/auth/session has not resolved yet" and "the backend has not shipped
 *   the `capabilities` field yet" -- and answers TRUE for both. That is the
 *   opposite of every real authorization check in this codebase, and is only
 *   safe because rendering advice failing open just means an entry renders and
 *   its own fetch 403s: the status quo before any of this existed, not a new
 *   hole. It is also what keeps this INERT until the session response actually
 *   carries `user.capabilities`, instead of blanking a gated screen for every
 *   role the moment this ships.
 *
 * An EMPTY list is a resolved answer meaning "this account holds nothing", and
 * is not the same as null. Nothing downstream of this may itself become an
 * authorization decision -- every route keeps its own server-side gate.
 */
fun holdsCapability(capabilities: List<String>?, code: String): Boolean =
    capabilities?.contains(code) ?: true

object RetailSession {
    var isAdmin by mutableStateOf(false)
        private set

    /**
     * The signed-in account's own `retail.*` grants, or null for "not known".
     * Compose state, so a screen gated on [hasCapability] re-renders the
     * moment the session resolves rather than staying on whatever the
     * pre-login default happened to show.
     */
    var capabilities by mutableStateOf<List<String>?>(null)
        private set

    fun update(user: User?) {
        isAdmin = isAdminUser(user)
        capabilities = user?.capabilities
    }

    fun hasCapability(code: String): Boolean = holdsCapability(capabilities, code)

    fun reset() {
        isAdmin = false
        // Back to "not known", not to "holds nothing". Logging out does not
        // teach us anything about the next account, and the honest default
        // for an unknown account is the one [holdsCapability] documents.
        capabilities = null
    }
}
