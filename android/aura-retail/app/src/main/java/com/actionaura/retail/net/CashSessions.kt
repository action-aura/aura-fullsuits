package com.actionaura.retail.net

import com.google.gson.JsonObject

/**
 * Cash sessions (the drawer) -- wire models for the routes the desktop's
 * frontend/cash-drawer.js already drives. All arithmetic (expected cash,
 * variance, what counts as a cash sale) is the SERVER's; these classes only
 * carry what it answers.
 *
 *   POST cash-sessions/open              {opening_float}
 *   GET  cash-sessions/current           -> {data: session | null, terminal_short, other_terminals_open}
 *   GET  cash-sessions?limit=N           -> {data: [session], may_approve}
 *   POST cash-sessions/{id}/movements    {type, amount, reason, gl_account_code}
 *   GET  cash-sessions/{id}/x-report     -> {data: report}
 *   POST cash-sessions/{id}/close        {closing_float_counted}
 *   POST cash-sessions/{id}/approve      {} or {acknowledge_uncounted: true}
 *
 * Every field is defaulted/nullable: an older embedded backend that lacks one
 * must degrade to "not shown", not crash the drawer screen.
 */
data class CashSession(
    val id: String = "",
    val status: String? = null,
    val opening_float: Double = 0.0,
    val opened_at: String? = null,
    val ended_at: String? = null,
    val closing_float_counted: Double? = null,
    val closing_float_expected: Double? = null,
    val variance: Double? = null,
    /** pending | not_counted | unverified | approved -- the server's words. */
    val variance_status: String? = null,
    val is_this_terminal: Boolean = false,
    val terminal_short: String? = null,
)

data class CurrentCashSessionFull(
    val status: String = "",
    val data: CashSession? = null,
    val terminal_short: String? = null,
    val other_terminals_open: Int = 0,
)

/** `may_approve` is the server's answer to "does THIS session hold retail.cash.approve". */
data class CashSessionListResponse(
    val status: String = "",
    val data: List<CashSession> = emptyList(),
    val may_approve: Boolean = false,
)

data class OpenCashSessionRequest(val opening_float: Double)
data class CashSessionResponse(val status: String = "", val message: String? = null, val data: CashSession? = null)

/**
 * Body of POST cash-sessions/{id}/movements -- the desktop's `_submitMovement`:
 * `{type, amount, reason}` plus `gl_account_code` ONLY for paid_in / paid_out
 * (MONEY-4b: that choice is the book entry). Gson omits a null, so a float
 * movement carries no `gl_account_code` key at all.
 */
data class CashMovementRequest(
    val type: String,
    val amount: Double,
    val reason: String = "",
    val gl_account_code: String? = null,
)

data class CashMovementResponse(val status: String = "", val message: String? = null, val data: JsonObject? = null)

data class CashMovementTotals(
    val float_in: Double = 0.0,
    val float_out: Double = 0.0,
    val paid_in: Double = 0.0,
    val paid_out: Double = 0.0,
)

data class XReport(
    val opening_float: Double = 0.0,
    val cash_sales: Double = 0.0,
    val customer_cash_in: Double = 0.0,
    val cash_refunds: Double = 0.0,
    val movements: CashMovementTotals = CashMovementTotals(),
    val expected_cash: Double = 0.0,
    val tender_totals: Map<String, Double> = emptyMap(),
    val variance: Double? = null,
    val variance_status: String? = null,
)

data class XReportResponse(val status: String = "", val message: String? = null, val data: XReport? = null)

data class CloseCashSessionRequest(val closing_float_counted: Double)
data class CloseCashSessionResult(val session: CashSession? = null, val report: XReport? = null)
data class CloseCashSessionResponse(val status: String = "", val message: String? = null, val data: CloseCashSessionResult? = null)

/** `{}` normally; `{acknowledge_uncounted: true}` for a force-closed, never-counted drawer. */
data class ApproveCashSessionRequest(val acknowledge_uncounted: Boolean? = null)
data class ApproveCashSessionResponse(val status: String = "", val message: String? = null, val data: JsonObject? = null)
