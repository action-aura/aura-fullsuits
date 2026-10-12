package com.actionaura.retail.ui.pos

import com.actionaura.retail.net.CashMovementRequest
import com.actionaura.retail.net.CashSession
import com.actionaura.retail.net.XReport

/**
 * The drawer's rules the phone has to apply BEFORE it sends anything. Every
 * figure that matters (expected cash, variance) is computed by the server; this
 * file decides only what the cashier is asked, what is sent, and what is shown
 * when -- all of it a mirror of frontend/cash-drawer.js.
 */

enum class MovementType(val wire: String) {
    FLOAT_IN("float_in"),
    FLOAT_OUT("float_out"),
    PAID_IN("paid_in"),
    PAID_OUT("paid_out"),
}

/** One row of the "What for?" select: the GL account code and its English label (the key tr() translates). */
data class Purpose(val code: String, val label: String)

/**
 * MONEY-4b (retail v52): what a paid out / paid in was FOR. The choice IS the
 * book entry -- the server posts the movement against this account. The codes
 * are the server's PAID_OUT_ACCOUNT_CODES / PAID_IN_ACCOUNT_CODES and it
 * refuses any other (400 INVALID_GL_ACCOUNT), so a drift here can only turn
 * into a refusal, never into a wrong booking. NOTE 4800 is "Other income"; 4900
 * is Sales Returns and Allowances and must never appear in a paid-in list.
 * Float in / float out ask nothing -- cash moving between the safe and the
 * drawer is not a cost.
 */
val PAID_OUT_PURPOSES: List<Purpose> = listOf(
    Purpose("6000", "Rent"),
    Purpose("6100", "Utilities"),
    Purpose("6200", "Salaries and wages"),
    Purpose("6300", "Transport and delivery"),
    Purpose("6400", "Marketing"),
    Purpose("6500", "Bank and card fees"),
    Purpose("6900", "Other expenses"),
    Purpose("3100", "Owner took it home (drawings)"),
)

val PAID_IN_PURPOSES: List<Purpose> = listOf(
    Purpose("4800", "Other income"),
    Purpose("3100", "Owner put money back (drawings)"),
)

/** The purposes a movement type must choose from; null when it asks nothing. */
fun purposesFor(type: MovementType): List<Purpose>? = when (type) {
    MovementType.PAID_OUT -> PAID_OUT_PURPOSES
    MovementType.PAID_IN -> PAID_IN_PURPOSES
    MovementType.FLOAT_IN, MovementType.FLOAT_OUT -> null
}

sealed interface MovementCheck {
    data class Ok(val request: CashMovementRequest) : MovementCheck
    data object BadAmount : MovementCheck
    /** Paid in / paid out without a "What for?" -- or one from the wrong list. */
    data object PurposeRequired : MovementCheck
}

/**
 * Validate and build a movement request.
 *  - the amount must be a positive number with no more decimals than the currency;
 *  - paid_in / paid_out REQUIRE a purpose from THEIR OWN list (a paid-in can
 *    never carry an expense code, and a paid-out can never carry 4800);
 *  - float_in / float_out send NO account, even if one is passed in: the
 *    server posts nothing for them, and a stale selection must not ride along.
 */
fun buildMovementRequest(
    type: MovementType,
    amountText: String?,
    reason: String,
    purposeCode: String?,
    dp: Int,
): MovementCheck {
    val minor = parseAmountMinor(amountText, dp) ?: return MovementCheck.BadAmount
    val allowed = purposesFor(type)
    val account: String? = if (allowed == null) null else {
        purposeCode?.takeIf { code -> allowed.any { it.code == code } } ?: return MovementCheck.PurposeRequired
    }
    return MovementCheck.Ok(
        CashMovementRequest(
            type = type.wire,
            amount = fromMinor(minor, dp),
            reason = reason.trim().take(200),
            gl_account_code = account,
        )
    )
}

/**
 * UI-2's blind count. The cashier counts FIRST; the expected figure and the
 * variance derived from it are shown only AFTER a count has been entered AND
 * only to a session the server says may approve (`may_approve`, retail.cash.approve).
 * Everyone else is never shown either number: the server records the variance
 * regardless and an approver reviews it. Without this a cashier who is short
 * simply types the expected figure into the count box.
 *
 * `mayApprove` must FAIL CLOSED: an unanswered question is "no", so a failed
 * request counts blind rather than leaking the figure.
 */
data class CloseReveal(val showExpected: Boolean, val showVariance: Boolean)

fun closeReveal(hasCount: Boolean, mayApprove: Boolean): CloseReveal =
    CloseReveal(showExpected = hasCount && mayApprove, showVariance = hasCount && mayApprove)

/**
 * This terminal's last CLOSING COUNT, used to pre-fill the next opening float.
 * Only the most recent ENDED drawer of THIS terminal counts: if it was
 * force-closed and never counted there is no honest number to carry, and falling
 * back to an older count would pre-fill a float a whole shift out of date.
 * (frontend/cash-drawer.js `_lastClosingCount`.) Null means "start at 0".
 */
fun lastClosingCount(sessions: List<CashSession>): Double? {
    val last = sessions.firstOrNull { it.is_this_terminal && !it.variance_status.isNullOrEmpty() && it.variance_status != "pending" }
        ?: return null
    val n = last.closing_float_counted ?: return null
    return n.takeIf { it.isFinite() && it >= 0.0 }
}

/** Ended drawers an approver can still accept: counted-but-unverified, or never counted. */
fun approvableSessions(sessions: List<CashSession>, mayApprove: Boolean): List<CashSession> =
    if (!mayApprove) emptyList()
    else sessions.filter { it.status != "open" && (it.variance_status == "unverified" || it.variance_status == "not_counted") }

/**
 * The X report as the screen may keep it. For someone who may not approve the
 * count, the expected-cash total (and any variance derived from it) is REMOVED
 * before the report is stored in screen state, not merely hidden by the layout:
 * a blind count that leaves the answer sitting in a state object is one
 * recomposition away from showing it. (Honest limit, same as the desktop's
 * UI-2: the server's x-report route still returns the figure to a closer.)
 */
fun redactXReport(report: XReport, mayApprove: Boolean): XReport =
    if (mayApprove) report else report.copy(expected_cash = 0.0, variance = null, variance_status = null)
