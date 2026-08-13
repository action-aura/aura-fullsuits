# Phase 9.5D — Invoice Status and Balance Contract

## Status derivation (forward reference — real implementation lands in Milestone 11)

`docs/owner/phase9_5a/payment-and-fulfillment-contract.md`'s `resolve_invoice_status()` formula is the design authority, extended (per `commercial-funnel-contract.md`'s Milestone 11 note) to sum `PaymentAllocation.allocated_amount` rather than raw `PaymentRecord` amounts:

```python
def resolve_invoice_status(*, current_status, invoice_total, allocated_payment_sum) -> str:
    if current_status in ("VOID", "DRAFT"):
        return current_status
    if allocated_payment_sum <= 0:
        return "ISSUED"
    if allocated_payment_sum < invoice_total:
        return "PARTIALLY_PAID"
    return "PAID"
```

This function already exists in `app/commercial_sales/calculator.py` (built in Milestone 3, ahead of the documents that consume it) — Milestone 9 does not re-derive it, and Milestone 11 will be the first to actually call it from a real allocation-mutation code path. `confirmed_allocated_amount()` (this milestone, `invoices.py`) is the query Milestone 11's allocation service will feed into that call.

## Outstanding vs. collected — never a second stored source of truth

`CommercialInvoice.total` is set once, at creation (Non-Negotiable Rule 10 — snapshotted from the Order, itself snapshotted from the Quote). There is no separate `amount_paid`/`outstanding_amount` column stored on the model — both are always *computed* from `confirmed_allocated_amount()` at read time, never persisted redundantly (Non-Negotiable Rule 3: outstanding invoice amount and collected amount must remain separate concepts, computed independently, never conflated into a single stored field that could drift out of sync with the real allocation ledger).

## Refund layering (forward reference to Milestone 12)

`REFUNDED`/`PARTIALLY_REFUNDED` states layer on top of the computed base once a `CommercialRefund` with `status == "PAID"` exists against the invoice — not implemented this milestone, tracked as Milestone 12's job per the original Phase 9.5A design note this document inherits unchanged.
