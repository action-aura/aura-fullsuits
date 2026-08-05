package com.actionaura.retail.presentation

/**
 * M6.9 -- the shared form-field state contract. Every M6+ form
 * (Category/Branch this milestone; Product/Supplier/Customer/inventory
 * adjustment/Import mapping/Settings in later milestones) builds its
 * own `UiState` out of these per real field, never a screen-specific
 * ad hoc shape.
 *
 * `value`/`displayValue` are deliberately separate for numeric fields:
 * `displayValue` is the raw text the user is currently typing (may be
 * transiently invalid, e.g. `"12."`  mid-edit); `value` is the last
 * successfully-parsed real domain value (`Money`/`Quantity`/
 * `PercentageRate`/etc, via M6.9's own "parse through M3 domain
 * parsers, never silently coerce invalid input to zero" rule). A field
 * the user has not yet finished typing a valid number for keeps its
 * last-good `value` and shows `domainError` instead of guessing.
 */
data class FormFieldState<T>(
    val value: T?,
    val displayValue: String,
    val touched: Boolean = false,
    val domainError: UiFieldError? = null,
    val required: Boolean = false,
    val enabled: Boolean = true,
) {
    /** Real, structural validity: a required field with no real parsed value, or any field with a live domain error, is invalid -- never inferred from `displayValue` alone. */
    val isValid: Boolean get() = domainError == null && (!required || value != null)

    companion object {
        fun <T> empty(required: Boolean = false): FormFieldState<T> = FormFieldState(value = null, displayValue = "", required = required)
    }
}

/** Real, shared submit-gate: a form may only submit once every real field it owns is valid -- never a partial/best-effort submit. */
fun List<FormFieldState<*>>.allValid(): Boolean = all { it.isValid }
