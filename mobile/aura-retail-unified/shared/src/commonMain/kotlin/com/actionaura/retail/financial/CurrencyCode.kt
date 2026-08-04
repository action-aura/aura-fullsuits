package com.actionaura.retail.financial

/** ISO-4217-shaped currency code, e.g. "USD"/"JOD". Not a decimal type -- travels alongside Money values. */
data class CurrencyCode(val code: String) {
    init {
        require(code.length in 3..3 && code.all { it.isLetter() }) { "Invalid currency code: $code" }
    }

    override fun toString(): String = code
}
