package com.actionaura.retail.ui.pos

import com.actionaura.retail.net.ApprovalReq
import com.actionaura.retail.net.CreateSaleRequest
import com.actionaura.retail.net.SaleItemReq

/**
 * Builds the `POST /sales` body. One place, so "what does a phone sale look
 * like on the wire" has one answer that a unit test can compare with what the
 * desktop sends.
 *
 * TWO SHAPES, and only two:
 *
 *  1. A single-method sale (every sale before this wave) -- byte for byte what
 *     the phone has always sent: `payment_method` is the chosen method,
 *     `amount_paid` is omitted unless it is a credit down-payment, no
 *     `tenders` key at all. A shop that never splits sees nothing change.
 *
 *  2. A split sale (two or more parts) -- the desktop's shape: `payment_method:
 *     "split"`, `tenders: [{method, amount}]`, and NO `amount_paid` (the server
 *     settles paid / change / balance from the parts; sending a
 *     client-computed amount here is the "cash trap" retail_api.py warns of).
 *
 * One part is NOT a split: it is the ordinary single-method sale spelled
 * differently, and the server itself rewrites a one-entry `tenders` back into
 * the legacy fields. The phone does the same up front so the request that goes
 * out for it is the unchanged one.
 *
 * Totals, tax and discount AMOUNTS are never fields here (CreateSaleRequest
 * does not have them; SaleContractTest pins that).
 */
fun buildCreateSaleRequest(
    items: List<SaleItemReq>,
    customerId: String?,
    idempotencyKey: String,
    pointsRedeemed: Int,
    singleMethod: String,
    creditDownPayment: Double?,
    splitParts: List<TenderPartMinor>,
    dp: Int,
    // ANDROID-3 (UI-3a): a sale-level discount and who approved it, as intent (see CartDiscount.kt).
    // All default to "none", so every earlier caller and request is unchanged.
    cartDiscount: CartDiscount? = null,
    discountReason: String? = null,
    approval: ApprovalReq? = null,
): CreateSaleRequest =
    if (splitParts.size >= 2) {
        CreateSaleRequest(
            amount_paid = null,
            payment_method = "split",
            items = items,
            idempotency_key = idempotencyKey,
            customer_id = customerId,
            points_redeemed = pointsRedeemed,
            tenders = toWireTenders(splitParts, dp),
            cart_discount_amount = discountAmount(cartDiscount, dp), cart_target_total = targetTotal(cartDiscount, dp),
            discount_reason = discountReason, approval = approval,
        )
    } else {
        CreateSaleRequest(
            amount_paid = creditDownPayment,
            payment_method = singleMethod,
            items = items,
            idempotency_key = idempotencyKey,
            customer_id = customerId,
            points_redeemed = pointsRedeemed,
            cart_discount_amount = discountAmount(cartDiscount, dp), cart_target_total = targetTotal(cartDiscount, dp),
            discount_reason = discountReason, approval = approval,
        )
    }

// Exactly one of the two is ever non-null (the server refuses both: DISCOUNT_AMBIGUOUS), and the
// number sent is the cashier's own, in the currency's decimals -- not a computed figure.
private fun discountAmount(d: CartDiscount?, dp: Int): Double? =
    d?.takeIf { it.kind == CartDiscountKind.AMOUNT }?.let { fromMinor(it.minor, dp) }

private fun targetTotal(d: CartDiscount?, dp: Int): Double? =
    d?.takeIf { it.kind == CartDiscountKind.TARGET }?.let { fromMinor(it.minor, dp) }
