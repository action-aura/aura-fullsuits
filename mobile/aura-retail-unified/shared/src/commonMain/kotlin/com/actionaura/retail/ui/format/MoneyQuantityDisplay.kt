package com.actionaura.retail.ui.format

import com.actionaura.retail.financial.CurrencyCode
import com.actionaura.retail.financial.Money
import com.actionaura.retail.financial.PercentageRate
import com.actionaura.retail.financial.Quantity

/**
 * M6.10 -- real, shared, PRESENTATION-ONLY formatters for exact
 * financial types. Every function here takes the real, already-exact
 * `Money`/`Quantity`/`PercentageRate` (M3's own canonical types) and
 * returns a display `String` ONLY -- never the reverse, and the
 * formatted string is never fed back into a calculation
 * (`money-quantity-presentation.md`'s own explicit rule). No `Double`
 * appears anywhere in this file.
 */

/** Real, explicit-currency display -- `Money.toString()` alone is the exact decimal amount with no currency symbol; this pairs it with the real `CurrencyCode`, never assumes/hardcodes one. */
fun formatMoney(amount: Money, currency: CurrencyCode): String = "${currency.code} ${amount}"

/** Real, currency-less display for a delta/adjustment where the sign itself carries meaning (e.g. a stock-value change) -- explicit sign always shown, matching M6.10's own "signed stock changes" requirement; `Money` has no public sign accessor, so the sign is read from the real decimal string's own leading character. */
fun formatSignedMoney(amount: Money, currency: CurrencyCode): String {
    val text = amount.toString()
    val signed = if (text.startsWith("-") || text.startsWith("+")) text else "+$text"
    return "${currency.code} $signed"
}

/** Real, exact quantity display -- no unit suffix here (units vary per product, M3 has no `Unit` concept baked into `Quantity` itself); callers append the real product's own unit string separately. */
fun formatQuantity(quantity: Quantity): String = quantity.toString()

/** Real, signed quantity display for stock-change deltas. */
fun formatSignedQuantity(quantity: Quantity): String {
    val text = quantity.toString()
    return if (text.startsWith("-") || text.startsWith("+")) text else "+$text"
}

/** Real percentage display -- `PercentageRate` stores the raw percentage value (e.g. "15" for 15%, not "0.15"), confirmed by its own real M3 contract; this only appends the "%" glyph, never rescales. */
fun formatPercentage(rate: PercentageRate): String = "${rate}%"
