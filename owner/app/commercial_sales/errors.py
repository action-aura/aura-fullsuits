"""Phase 9.5D -- stable-code exceptions for the commercial-sales service
layer (Quote/SalesOrder/CommercialInvoice/Payment/Refund/Fulfillment/
Commission).

Reuses the shared StableCodeError base from commercial_ops (Phase
9.5B-R3) rather than defining a second copy -- same request-context-free
contract: raise with a stable code + params, str(exc) is always a real
English diagnostic, localization happens only at the route boundary via
app.i18n_labels. Matches app/leads/errors.py's pattern exactly (not
commercial_ops/renewal_requests.py's older, pre-extraction
InvalidRenewalTransitionError, which hand-rolls the same shape without
subclassing this base).
"""
from __future__ import annotations

from app.commercial_ops.errors import StableCodeError

QUOTE_TRANSITIONS: dict[str, set[str]] = {
    "DRAFT": {"SENT", "CANCELLED"},
    "SENT": {"ACCEPTED", "REJECTED", "EXPIRED", "CANCELLED"},
    "ACCEPTED": set(),
    "REJECTED": set(),
    "EXPIRED": set(),
    "CANCELLED": set(),
}

SALES_ORDER_TRANSITIONS: dict[str, set[str]] = {
    "DRAFT": {"CONFIRMED", "CANCELLED"},
    "CONFIRMED": {"FULFILLED", "CANCELLED"},
    "FULFILLED": set(),
    "CANCELLED": set(),
}

INVOICE_TRANSITIONS: dict[str, set[str]] = {
    "DRAFT": {"ISSUED", "VOID"},
    "ISSUED": {"VOID", "PARTIALLY_PAID", "PAID"},
    "PARTIALLY_PAID": {"PAID", "PARTIALLY_REFUNDED"},
    "PAID": {"PARTIALLY_REFUNDED", "REFUNDED"},
    "PARTIALLY_REFUNDED": set(),
    "REFUNDED": set(),
    "VOID": set(),
}

REFUND_TRANSITIONS: dict[str, set[str]] = {
    "DRAFT": {"APPROVED", "VOID"},
    "APPROVED": {"PAID", "VOID"},
    "PAID": set(),
    "VOID": set(),
}

APPROVAL_TRANSITIONS: dict[str, set[str]] = {
    "PENDING": {"APPROVED", "REJECTED", "CANCELLED", "EXPIRED"},
    "APPROVED": set(),
    "REJECTED": set(),
    "CANCELLED": set(),
    "EXPIRED": set(),
}


class CommercialSalesError(StableCodeError):
    _MESSAGES = {
        # Financial calculation (Milestone 3)
        "INVALID_QUANTITY": "Quantity must be a positive whole number.",
        "NON_FINITE_AMOUNT": "Amount must be a finite number.",
        "NEGATIVE_DOCUMENT_TOTAL": "Document total cannot be negative.",
        "DISCOUNT_EXCEEDS_GROSS": "Discount cannot exceed the applicable gross amount.",
        "REFUND_EXCEEDS_REFUNDABLE": "Refund amount ({amount}) exceeds the refundable balance ({refundable}).",
        "ALLOCATION_EXCEEDS_PAYMENT": "Allocation amount ({amount}) exceeds the unallocated payment balance ({available}).",
        "ALLOCATION_EXCEEDS_OUTSTANDING": "Allocation amount ({amount}) exceeds the invoice outstanding balance ({outstanding}).",
        "CURRENCY_MISMATCH": "Currency {given} does not match the required currency {expected}.",
        "INVALID_CURRENCY_CODE": "Currency must be a 3-letter ISO 4217 code.",
        "EMPTY_DOCUMENT": "A document must have at least one line.",
        # Transitions
        "INVALID_QUOTE_TRANSITION": "Cannot change quote status from {from_status} to {to_status}.",
        "INVALID_ORDER_TRANSITION": "Cannot change order status from {from_status} to {to_status}.",
        "INVALID_INVOICE_TRANSITION": "Cannot change invoice status from {from_status} to {to_status}.",
        "INVALID_REFUND_TRANSITION": "Cannot change refund status from {from_status} to {to_status}.",
        "INVALID_APPROVAL_TRANSITION": "Cannot change approval status from {from_status} to {to_status}.",
        "STALE_VERSION": "This record was changed by someone else. Reload and try again.",
        "RECORD_NOT_FOUND": "Record not found.",
        "RECORD_ACCESS_DENIED": "You do not have access to this record.",
        "IDEMPOTENCY_CONFLICT": "This request conflicts with an earlier request using the same idempotency key.",
        "REASON_REQUIRED": "A reason is required for this action.",
        "INVALID_PAYMENT_METHOD": "Payment method must be one of the allowed values.",
        # Approvals / segregation of duties
        "SELF_APPROVAL_FORBIDDEN": "You cannot approve your own request.",
        "APPROVAL_REQUIRED": "This action requires approval before it can proceed.",
        "APPROVAL_STALE": "The approval no longer matches the current version of this record.",
        "DISCOUNT_LIMIT_EXCEEDED": "Discount exceeds your permitted limit and requires approval.",
        "PRICE_OVERRIDE_REQUIRES_APPROVAL": "A custom price requires approval.",
        "ZERO_PRICE_REQUIRES_APPROVAL": "A zero-price line requires approval.",
        # Quote/order/customer boundary
        "QUOTE_NOT_ACCEPTED": "The quote must be accepted before an order can be created.",
        "CUSTOMER_REQUIRED": "A confirmed customer is required before an order can be created.",
        "CATALOG_ITEM_INACTIVE": "This product or plan is not currently sellable.",
        "PRICE_VERSION_EXPIRED": "This price is no longer effective.",
        # Payments
        "PAYMENT_NOT_CONFIRMED": "The payment must be confirmed before it can be allocated.",
        "PAYMENT_ALREADY_CONFIRMED": "This payment has already been confirmed.",
        "SELF_CONFIRMATION_FORBIDDEN": "You cannot confirm a payment you submitted yourself.",
        # Fulfillment
        "FULFILLMENT_NOT_ELIGIBLE": "This order is not eligible for fulfillment yet: {reason}.",
        "FULFILLMENT_ALREADY_COMPLETE": "This order line has already been fulfilled.",
        # Commission
        "COMMISSION_RULE_TYPE_NOT_IMPLEMENTED": "Commission rule type {rule_type} is not implemented in this phase.",
    }
