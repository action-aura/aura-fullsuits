package com.actionaura.retail.ui

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import com.actionaura.retail.net.SessionResponse
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
 * "Create, disable or reconfigure an employee account, its till PIN, or this
 * device's branch pin" -- the owner-only capability every route under
 * `/api/admin/employees` and `POST /api/sub/retail/device/branch` gates on
 * server-side (`commercial_runtime/identity/user_accounts.py::CAP_EMPLOYEES`).
 * Deliberately excluded from `ROLE_MANAGER`'s grant set in that module: both
 * managing accounts and pinning a till are owner-only administrative acts,
 * not something a manager's role includes by default.
 *
 * Spelled here as a constant for the same reason [CAP_REPORTS] is: a
 * capability code that has drifted from the server's does not fail loudly --
 * it silently hides a control from the wrong role, or shows it to everybody.
 */
const val CAP_EMPLOYEES = "retail.employees"

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

/**
 * The grant list carried by a `GET /api/auth/session` body, or null when the
 * body carries none.
 *
 * Extracted into its own named function -- not inlined at the call site -- for
 * exactly the reason app-shell.js gives for having done the same thing after
 * paying for this bug on the desktop: it is the one line in the client whose
 * correctness cannot be established by reading it, because the question is
 * about a different file in a different language, and the answer was NO for
 * the entire life of the capability feature.
 *
 * THE BUG: this client declared `capabilities` on the [User] model only.
 * `commercial_runtime/identity/onboarding_routes.py::get_session` returns it as
 * a TOP-LEVEL key, a SIBLING of `user`, never a member of it. A real cashier's
 * response body is:
 *
 *     {"authenticated": true,
 *      "capabilities": ["retail.cash.close", "retail.refund", "retail.sell"],
 *      "is_mt": true, "language": "en",
 *      "user": {"id": "...", "role": "cashier", "email": "...", ...}}
 *
 * So `user.capabilities` was permanently null, [holdsCapability] fell open on
 * null (correctly, and by design), and `hasCapability(CAP_REPORTS)` answered
 * TRUE for an account granted none of it. EmployeeSalesScreen's gate and
 * ReportsScreen's By-Employee entry were both written, both shipped, and
 * neither ever refused anybody -- and nothing was red, because a gate that
 * never engages is indistinguishable from a gate on a permissive account.
 *
 * BOTH locations are accepted, top-level first, for the same reason the desktop
 * accepts both: the clinic product shares this identity stack on a separate
 * code path, and `user` is the obvious place for a future contributor to add
 * the key. It costs one null check and removes a failure mode whose whole
 * character is that it is silent.
 *
 * The distinction [holdsCapability] rests on is preserved end to end: an absent
 * key and an explicit JSON null both yield null ("not known", fail open), while
 * an EMPTY list is passed through untouched as the resolved answer "this
 * account holds nothing". `?:` is the operator that gets this right --
 * `emptyList()` is not null, so it is never folded into the unknown bucket.
 * (The desktop's twin has to write `Array.isArray` for the same reason: `[]` is
 * truthy in JavaScript, and `if (!caps)` is precisely how that trap gets set.)
 */
fun sessionCapabilities(session: SessionResponse?): List<String>? =
    session?.capabilities ?: session?.user?.capabilities

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

    /**
     * Adopt everything a `/api/auth/session` body tells us about the signed-in
     * account. THE entry point -- [update] below is the login-response-only
     * partial, and cannot resolve capabilities because
     * `commercial_runtime/identity/auth_routes.py`'s login handler does not
     * send them (grep it: the word does not appear in that file).
     */
    fun adopt(session: SessionResponse?) {
        isAdmin = isAdminUser(session?.user)
        capabilities = sessionCapabilities(session)
    }

    /**
     * Admin state from a LOGIN response, which carries `user` and nothing else.
     * Deliberately leaves [capabilities] alone rather than clearing it: the
     * login body has no opinion on grants, and writing null here would be that
     * absence of an opinion masquerading as "not known" -- harmless today
     * (it fails open) but the exact shape of the bug this file just fixed.
     * The caller follows this with [adopt] over a real session fetch.
     */
    fun update(user: User?) {
        isAdmin = isAdminUser(user)
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
