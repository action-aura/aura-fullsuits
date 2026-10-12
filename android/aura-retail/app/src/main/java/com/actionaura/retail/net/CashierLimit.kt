package com.actionaura.retail.net

/**
 * The shop's cashier discount limit, read from the SAME `settings/credit`
 * endpoint the credit screen uses -- but through its own tiny read-only class
 * on purpose.
 *
 * Adding `cashier_discount_limit_pct` to [CreditSettings] would have been the
 * obvious edit and a trap: [CreditSettings] is also the body the Settings
 * screen POSTs back (`creditSettingsSet`), and a field it never learned to
 * carry would be written back as its default, silently resetting the owner's
 * limit to 0 the next time anybody saved the credit settings. This class is
 * only ever deserialised, never sent.
 *
 * The value arrives as a string straight off SQLite ("10", "7.5", "0").
 * Anything unreadable means "no limit" (0): a cashier then gets no discount
 * control, which is the safe direction.
 */
data class CashierLimitSettings(val cashier_discount_limit_pct: String? = null)

data class CashierLimitResponse(val status: String = "", val data: CashierLimitSettings? = null)

fun CashierLimitSettings?.limitPct(): Double =
    this?.cashier_discount_limit_pct?.trim()?.toDoubleOrNull()?.takeIf { it.isFinite() }?.coerceIn(0.0, 100.0) ?: 0.0
