package com.actionaura.retail.ui.pos

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
        )
    } else {
        CreateSaleRequest(
            amount_paid = creditDownPayment,
            payment_method = singleMethod,
            items = items,
            idempotency_key = idempotencyKey,
            customer_id = customerId,
            points_redeemed = pointsRedeemed,
        )
    }
