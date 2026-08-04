package com.actionaura.retail.financial

/**
 * Every stable code from docs/retail/unified_mobile/financial-error-code-map.md,
 * each cross-referenced there to either a real, exact Python condition
 * (LEGACY_PARITY) or a documented, named gap being closed
 * (CANONICAL_UNIFIED). `code` is the stable machine-readable identifier;
 * localization (English/Arabic prose) happens strictly outside this file,
 * in a later milestone's UI-facing error-message layer -- never here.
 */
sealed class FinancialError(val code: String) {
    data class InvalidQuantity(val rawValue: String) : FinancialError("INVALID_QUANTITY")
    data class NonPositiveQuantity(val rawValue: String) : FinancialError("NON_POSITIVE_QUANTITY")
    data class InvalidPrice(val rawValue: String) : FinancialError("INVALID_PRICE")
    data class NegativePrice(val rawValue: String) : FinancialError("NEGATIVE_PRICE")
    data class PaymentBelowTotalRequiresCustomer(val balanceDue: String) : FinancialError("CREDIT_SALE_REQUIRES_CUSTOMER")
    data class CreditModeDisallowed(val customerId: String) : FinancialError("CREDIT_MODE_DISALLOWED")
    data class CreditLimitExceeded(val limit: String, val outstanding: String, val adds: String) : FinancialError("CREDIT_LIMIT_EXCEEDED")
    data class ReturnExceedsSoldQuantity(val productId: String, val saleId: String) : FinancialError("RETURN_EXCEEDS_SOLD_QUANTITY")
    data class ReturnExceedsRemainingQuantity(val requested: String, val remaining: String) : FinancialError("RETURN_EXCEEDS_REMAINING_QUANTITY")
    data class RefundExceedsRefundableAmount(val requested: String, val refundable: String) : FinancialError("REFUND_EXCEEDS_REFUNDABLE_AMOUNT")
    data class InsufficientStock(val productName: String, val have: String, val requested: String) : FinancialError("INSUFFICIENT_STOCK")
    data class DuplicateOperationConflict(val idempotencyKey: String) : FinancialError("DUPLICATE_OPERATION_CONFLICT")
    data class NumericOverflow(val operation: String) : FinancialError("NUMERIC_OVERFLOW")
    data class ProductNotFound(val productId: String) : FinancialError("PRODUCT_NOT_FOUND")
    data class ProductInactive(val productName: String) : FinancialError("PRODUCT_INACTIVE")
    data class SaleNotFound(val saleId: String) : FinancialError("SALE_NOT_FOUND")
    object EmptySale : FinancialError("EMPTY_SALE")
    object EmptyReturn : FinancialError("EMPTY_RETURN")
}
