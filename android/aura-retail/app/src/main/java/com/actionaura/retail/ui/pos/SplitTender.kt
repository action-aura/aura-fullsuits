package com.actionaura.retail.ui.pos

import com.actionaura.retail.net.TenderPart

/**
 * Split tender -- one sale paid by several methods (card 5.000 + cash 6.832).
 *
 * The SERVER is the authority on what each part is worth: it settles the parts
 * in `core/retail/tenders.py::settle_tenders`, decides change, writes one
 * `payments` row per part, and a cash part counts toward the drawer exactly as
 * a single-cash sale does. This file only does the two things the phone has to
 * do before it can send anything -- keep the entered amounts exact (minor
 * units) and tell the cashier, on the screen, how much is left and whether the
 * Charge button may be pressed. Every rule below is a mirror of a refusal the
 * server would otherwise make AFTER the customer has handed the money over;
 * the mirror exists so the cashier hears it before, and it is NOT a second
 * source of truth (the server re-checks everything and wins any disagreement).
 *
 * THE WIRE SHAPE is the desktop's, not a phone invention: `payment_method:
 * "split"` plus `tenders: [{method, amount}]`, and NO `amount_paid` -- the
 * server derives paid / change / balance from the parts
 * (`_buildTenderPayload` + `_checkout` in frontend/subsystem-retail.js). A
 * single-method sale never goes through here and keeps today's shape.
 *
 * An amount on a CASH part is the money HANDED OVER (it may exceed what is
 * due; the excess comes back as change). Any other method is charged for
 * exactly the amount entered.
 */
const val METHOD_CASH = "cash"
const val METHOD_CREDIT = "credit"

/** The server's MAX_TENDERS (core/retail/tenders.py). */
const val MAX_TENDER_PARTS = 6

data class TenderPartMinor(val method: String, val minor: Long)

/** Why a set of parts cannot be charged yet; the same cases the server refuses. */
enum class SplitProblem {
    /** No parts at all. */
    NO_PARTS,
    /** More parts than the server accepts. */
    TOO_MANY_PARTS,
    /** The parts add up to less than what is due (server: TENDERS_SHORT). */
    SHORT,
    /** More than due with a part on account in it (server: TENDER_CREDIT_OVERPAID). */
    CREDIT_OVERPAID,
    /** More than due and the surplus is larger than the cash part (server: TENDER_OVERPAID_NON_CASH). */
    NON_CASH_OVERPAID,
    /** The cash part would all come back as change (server: TENDER_CASH_NOT_NEEDED). */
    CASH_NOT_NEEDED,
    /** A part on account needs a named customer (the server's credit rule). */
    CREDIT_NEEDS_CUSTOMER,
}

data class SplitSummary(
    val dueMinor: Long,
    val enteredMinor: Long,
    /** What is still to take; never negative. */
    val remainingMinor: Long,
    /** Change to hand back; only a cash part can produce any. */
    val changeMinor: Long,
    val problem: SplitProblem?,
) {
    /** True when the parts may be sent. */
    val chargeable: Boolean get() = problem == null
}

/**
 * Add [minor] of [method] to [parts]. The server refuses the same method twice
 * (TENDER_DUPLICATE_METHOD), so a second entry of a method is merged into the
 * first -- the cashier typing "cash 5" then "cash 3" means "cash 8".
 * Returns a new list; the input is untouched.
 */
fun addTenderPart(parts: List<TenderPartMinor>, method: String, minor: Long): List<TenderPartMinor> {
    require(minor > 0) { "a tender part must be more than zero" }
    val key = method.trim().lowercase()
    val idx = parts.indexOfFirst { it.method == key }
    return if (idx < 0) parts + TenderPartMinor(key, minor)
    else parts.mapIndexed { i, p -> if (i == idx) p.copy(minor = p.minor + minor) else p }
}

fun removeTenderPart(parts: List<TenderPartMinor>, index: Int): List<TenderPartMinor> =
    parts.filterIndexed { i, _ -> i != index }

/**
 * Remaining, change and whether the parts can be charged, against [dueMinor].
 *
 * A mirror of `settle_tenders` in integer minor units. The server compares with
 * a half-quantum epsilon only because it is comparing Decimals that were
 * rounded to the quantum; in whole minor units the epsilon is zero, which is
 * the same comparison.
 */
fun summarizeSplit(dueMinor: Long, parts: List<TenderPartMinor>, hasCustomer: Boolean): SplitSummary {
    val entered = parts.sumOf { it.minor }
    val cash = parts.filter { it.method == METHOD_CASH }.sumOf { it.minor }
    val credit = parts.filter { it.method == METHOD_CREDIT }.sumOf { it.minor }
    val remaining = (dueMinor - entered).coerceAtLeast(0L)
    val over = (entered - dueMinor).coerceAtLeast(0L)
    // Only a cash part can come back as change, and never more than was given.
    val change = if (over > 0 && cash > 0) minOf(over, cash) else 0L

    val problem = when {
        parts.isEmpty() -> SplitProblem.NO_PARTS
        parts.size > MAX_TENDER_PARTS -> SplitProblem.TOO_MANY_PARTS
        credit > 0 && !hasCustomer -> SplitProblem.CREDIT_NEEDS_CUSTOMER
        entered < dueMinor -> SplitProblem.SHORT
        credit > 0 && over > 0 -> SplitProblem.CREDIT_OVERPAID
        over > cash -> SplitProblem.NON_CASH_OVERPAID
        cash > 0 && cash - change <= 0L -> SplitProblem.CASH_NOT_NEEDED
        else -> null
    }
    return SplitSummary(dueMinor, entered, remaining, change, problem)
}

/** The `tenders` array the server reads, in the order the cashier took them. */
fun toWireTenders(parts: List<TenderPartMinor>, dp: Int): List<TenderPart> =
    parts.map { TenderPart(method = it.method, amount = fromMinor(it.minor, dp)) }
