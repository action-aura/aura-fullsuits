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
 * "Create a product, or adjust stock on hand" -- the capability
 * `POST /api/sub/retail/products` and the stock-adjust route gate on
 * server-side (`commercial_runtime/identity/user_accounts.py::CAP_STOCK_ADJUST`,
 * `'retail.stock.adjust'`). `ROLE_MANAGER` and the owner hold it;
 * `ROLE_CASHIER` does not -- a cashier's grant set is sell/refund/cash-close
 * only.
 *
 * Spelled here as a constant for the same reason [CAP_REPORTS] is: a
 * capability code that has drifted from the server's does not fail loudly --
 * it silently shows a control to a role that can only watch it fail, or hides
 * it from a role that should have it. Pinned against the server's string by
 * ProductsWiringContractTest.
 */
const val CAP_STOCK_ADJUST = "retail.stock.adjust"

/**
 * "Give value away with no matching payment" -- discretionary manual
 * discounts AND loyalty-point redemption both ride this ONE capability
 * server-side (`commercial_runtime/identity/user_accounts.py::CAP_DISCOUNT`,
 * `'retail.discount'`). `retail_api.py`'s `create_sale` reuses it verbatim
 * for a `points_redeemed` request rather than minting a second gate --
 * "a redemption is exactly what CAP_DISCOUNT's own authority already
 * covers" is that route's own reasoning, in its own comment. `ROLE_MANAGER`
 * and the owner hold it; `ROLE_CASHIER` does not.
 *
 * Spelled here as a constant for the same reason [CAP_REPORTS] is: a
 * capability code that has drifted from the server's does not fail loudly --
 * it silently shows a control to a role that can only watch it 403, or
 * hides it from a role that should have it.
 */
const val CAP_DISCOUNT = "retail.discount"

/**
 * "Ring a sale at the till" -- `commercial_runtime/identity/user_accounts.py::
 * CAP_SELL` (`'retail.sell'`). Spelled here for one reason only: it is what
 * separates a person who belongs at the till from a books-only person (the
 * Accountant preset holds the books codes and NOT this one), see
 * [isBooksOnly]. Same drift hazard as [CAP_REPORTS]; pinned against the server
 * string by SessionCapabilityContractTest.
 */
const val CAP_SELL = "retail.sell"

/**
 * The four registry-v9 "books" capabilities, spelled exactly as
 * `user_accounts.py` spells them (CAP_BOOKS_VIEW .. CAP_BOOKS_CLOSE). The
 * Accountant preset grants view and record and neither of the other two; the
 * owner holds all four. The manager preset holds NO books code at all
 * (`ROLE_PRESETS[PRESET_MANAGER]` is `ROLE_CAPABILITIES[ROLE_MANAGER]`, which
 * carries none) -- this comment used to say approve and close were "held by
 * the owner and manager presets", which was wrong and would have sent a
 * reader looking for a bug in the manager grant. A code that drifts from the
 * server's does not fail loudly -- it hides the books from the one person
 * hired to read them, or routes a cashier to a screen that only 403s. Pinned
 * against the server by SessionCapabilityContractTest.
 */
const val CAP_BOOKS_VIEW = "retail.books.view"
const val CAP_BOOKS_RECORD = "retail.books.record"
const val CAP_BOOKS_APPROVE = "retail.books.approve"
const val CAP_BOOKS_CLOSE = "retail.books.close"

/** "Take money back / reverse a sale" -- `user_accounts.py::CAP_REFUND`. */
const val CAP_REFUND = "retail.refund"

/** "Close the till drawer" -- `user_accounts.py::CAP_CASH_CLOSE`. */
const val CAP_CASH_CLOSE = "retail.cash.close"

/** Every books code, in one place so [isBooksOnly] cannot forget one. */
val BOOKS_CAPABILITIES: List<String> =
    listOf(CAP_BOOKS_VIEW, CAP_BOOKS_RECORD, CAP_BOOKS_APPROVE, CAP_BOOKS_CLOSE)

/**
 * The codes a NORMAL tab (POS, Products, Customers) is there to serve: selling,
 * adjusting stock, refunding, discounting and closing the drawer. A person who
 * holds ANY of them has something to do on the normal shell, so they keep it
 * even if they also hold a books code and even if they lack [CAP_SELL].
 *
 * Before this list [isBooksOnly] asked only "no CAP_SELL", so a custom account
 * with books.view and stock.adjust (but not sell) was moved onto a two-tab
 * shell with no Products tab -- locked out of the one screen their own grant
 * exists for. Which means the books shell never has to ADD Products or
 * Customers tabs: anybody who would want them fails [isBooksOnly] and stays on
 * the shell that already has them.
 */
val TILL_CAPABILITIES: List<String> =
    listOf(CAP_SELL, CAP_STOCK_ADJUST, CAP_REFUND, CAP_DISCOUNT, CAP_CASH_CLOSE)

/**
 * True for a person who holds at least one books code and NONE of
 * [TILL_CAPABILITIES] -- the Accountant. Such a person must not land on the
 * till: the server would refuse every sale they rang (403), and a till screen
 * that refuses everything is a broken first impression for the one user who was
 * never meant to see it.
 *
 * Fails CLOSED to "not books-only" on null (capabilities not known yet):
 * the unknown state keeps the normal shell, exactly as [holdsCapability]'s
 * fail-open default keeps every gate inert until the session resolves. An
 * EMPTY list is a resolved "holds nothing" and is not books-only either --
 * there is no books access to route to. This is routing advice only; every
 * books route keeps its own server-side gate.
 */
fun isBooksOnly(capabilities: List<String>?): Boolean {
    if (capabilities == null) return false
    if (capabilities.any { it in TILL_CAPABILITIES }) return false
    return capabilities.any { it in BOOKS_CAPABILITIES }
}

/**
 * True if `capabilities` grants ANY of `codes` -- the multi-code twin of
 * [holdsCapability], with the same fail-open default on null (rendering advice
 * only; the server re-gates every call) and the same reading of an empty list
 * as "holds nothing". An empty `codes` list is a programming error and answers
 * false so it can never silently show a control to everybody.
 */
fun holdsAnyCapability(capabilities: List<String>?, codes: List<String>): Boolean =
    capabilities?.any { it in codes } ?: codes.isNotEmpty()

// ── Write gates, one per server decorator ────────────────────────────────────
//
// Each list is what the SERVER requires for the call the button makes, copied
// from the route's own decorator in `products/retail/backend/api/retail_api.py`
// and pinned to it by WriteGateContractTest (which reads the Python). They are
// rendering advice: a control the person cannot use is hidden instead of being
// offered and answered with a 403. The server re-gates every call regardless.

/** `POST /returns` -- `@mt_require_capability(CAP_REFUND)`. */
val CAPS_CREATE_RETURN: List<String> = listOf(CAP_REFUND)

/**
 * Hold, resume and discard a parked sale -- `POST /held-sales`,
 * `POST /held-sales/<id>/resume`, `DELETE /held-sales/<id>`: all
 * `@mt_require_capability(CAP_SELL)` (a held sale is a paused sale).
 */
val CAPS_HOLD_SALE: List<String> = listOf(CAP_SELL)

/**
 * The whole drawer on the phone -- open a shift, current, movements, X report, end
 * shift -- all `@mt_require_capability(CAP_CASH_CLOSE)` ("retail.cash.close covers the
 * WHOLE drawer", the route comments say). Accepting a variance is a separate authority
 * (retail.cash.approve) and is offered only when the server says `may_approve`.
 */
val CAPS_CASH_DRAWER: List<String> = listOf(CAP_CASH_CLOSE)

/**
 * Categories create/update/delete, `POST /suppliers`, `POST /purchase-orders`
 * and `POST /purchase-orders/<id>/receive` -- all
 * `@mt_require_capability(CAP_STOCK_ADJUST)`.
 */
val CAPS_STOCK_WRITE: List<String> = listOf(CAP_STOCK_ADJUST)

/** `POST /customers/<id>/payment` -- `mt_require_any_capability(CAP_SELL, CAP_BOOKS_RECORD)`. */
val CAPS_CUSTOMER_PAYMENT: List<String> = listOf(CAP_SELL, CAP_BOOKS_RECORD)

/** `POST /suppliers/<id>/payment` and `POST /purchase-orders/<id>/pay` -- books.record only. */
val CAPS_SUPPLIER_PAYMENT: List<String> = listOf(CAP_BOOKS_RECORD)

/**
 * `POST /payments/<id>/void` on a CUSTOMER payment -- the decorator floor,
 * `mt_require_any_capability(CAP_REFUND, CAP_BOOKS_RECORD)`.
 */
val CAPS_VOID_CUSTOMER_PAYMENT: List<String> = listOf(CAP_REFUND, CAP_BOOKS_RECORD)

/**
 * `POST /payments/<id>/void` on a SUPPLIER or PO payment -- narrowed to
 * books.record in the route body, so a refund-only cashier cannot undo money the
 * shop paid out.
 */
val CAPS_VOID_SUPPLIER_PAYMENT: List<String> = listOf(CAP_BOOKS_RECORD)

/** Route names shared by AppRoot.kt's nav graph and the routing contract test. */
const val ROUTE_DASHBOARD = "dashboard"
const val ROUTE_BOOKS_HOME = "books_home"

/**
 * The route the signed-in shell opens on: the read-only books home for a
 * books-only person, the dashboard (which sits beside the till) for everyone
 * else. One decision, one place, so the tab bar and the NavHost's start
 * destination cannot disagree about it.
 */
fun homeRouteFor(booksOnly: Boolean): String =
    if (booksOnly) ROUTE_BOOKS_HOME else ROUTE_DASHBOARD

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
     *
     * CLEARS [capabilities] to "not known". This used to leave them alone, on the
     * reasoning that the login body has no opinion on grants -- but the previous
     * account's list is a stale opinion, not an absence of one. Sign out as an
     * Accountant, sign in as a cashier without an intervening [reset] (an
     * expired session bounces straight to the login screen without one) and the
     * cashier briefly inherited the Accountant's grants: [isBooksOnly] still
     * answered true, so the cashier was routed to the books home until the
     * session fetch landed. Null is the fail-open default [holdsCapability]
     * documents, so a moment of "not known" only means the gates render inert
     * until [adopt] runs over a real session fetch -- which the caller does next.
     */
    fun update(user: User?) {
        isAdmin = isAdminUser(user)
        capabilities = null
    }

    fun hasCapability(code: String): Boolean = holdsCapability(capabilities, code)

    /** True when the account holds ANY of [codes] (see [holdsAnyCapability]). */
    fun hasAnyCapability(codes: List<String>): Boolean = holdsAnyCapability(capabilities, codes)

    /**
     * Books-only Accountant: routed to a read-only home instead of the till.
     * An admin is never books-only whatever the list says (the owner's own
     * grants always include selling; this is belt and braces against a
     * malformed list stranding the owner away from the till).
     */
    val isBooksOnly: Boolean
        get() = !isAdmin && com.actionaura.retail.ui.isBooksOnly(capabilities)

    fun reset() {
        isAdmin = false
        // Back to "not known", not to "holds nothing". Logging out does not
        // teach us anything about the next account, and the honest default
        // for an unknown account is the one [holdsCapability] documents.
        capabilities = null
    }
}
