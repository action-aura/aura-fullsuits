# Phase 9.5A — Payment and Fulfillment Contract

## Invoice status derivation (computed, not stored redundantly)

```python
def resolve_invoice_status(invoice, confirmed_payments_sum: Decimal) -> str:
    if invoice.status in ("VOID", "DRAFT"):
        return invoice.status  # terminal/pre-issuance states are never overridden by payment sums
    if confirmed_payments_sum <= 0:
        return "ISSUED"
    if confirmed_payments_sum < invoice.total:
        return "PARTIALLY_PAID"
    return "PAID"
```

Refund adjustments (`REFUNDED`/`PARTIALLY_REFUNDED`) layer on top of this same computed base once any
`CommercialRefund` with `status=PAID` exists against the invoice — full detail deferred to Milestone 22
implementation, the principle (computed, never a second stored source of truth) is fixed here.

## Fulfillment (Sales Order -> license/subscription issuance)

`SalesOrder.status -> FULFILLED` is set only after the real, existing Phase 6/8 subscription/license
issuance workflow completes for that customer — this phase does not change that workflow's own
conditions (plan selection, payment confirmation requirements, etc.), it only adds the bookkeeping link
(`SalesOrder.id` optionally referenced from the `Subscription` row created for it, one new nullable FK
on `Subscription`, additive).

## Refund is a real, separate immutable record

Never a negative `PaymentRecord` row, never an edit to the original payment — `CommercialRefund` is its
own document with its own `DRAFT -> APPROVED -> PAID` lifecycle, `refunds.create`/`refunds.approve`
permissions (management/finance only), and its own `REFUND_RECORDED` audit event.

## Idempotency and concurrency — concrete mechanism

Reuses the exact real pattern already proven in `owner/app/licensing/services.py`'s
`issue_license_key()`: a small idempotency-ledger table (`commercial_operations_idempotency_keys`:
`idempotency_key` unique, `operation_code`, `result_reference_id`, `created_at`) checked before any
sensitive write; a replayed request with the same key returns the original result without
re-executing side effects. One shared table across Quote/Order/Invoice/Payment/Refund/Commission
operations (not one table per operation type), keyed by `(idempotency_key, operation_code)` uniqueness.
