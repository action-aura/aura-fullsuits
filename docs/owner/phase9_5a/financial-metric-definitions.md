# Phase 9.5A — Financial Metric Definitions (exact, server-computed)

```python
def gross_invoiced(period) -> Decimal:
    # Sum of CommercialInvoice.total for every invoice with status in
    # (ISSUED, PARTIALLY_PAID, PAID, PARTIALLY_REFUNDED, REFUNDED) issued within the period.
    # DRAFT and VOID invoices are excluded -- an invoice that was never issued, or was voided,
    # was never really "invoiced."
    ...

def collected_revenue(period) -> Decimal:
    # Sum of confirmed PaymentRecord.amount linked to a CommercialInvoice, within the period,
    # MINUS sum of PAID CommercialRefund.amount within the period.
    # Never includes a PENDING/unconfirmed PaymentRecord, and never includes an ISSUED-but-unpaid
    # invoice's total -- this is the one metric the governing instruction explicitly warns about
    # ("Do not merge unpaid invoices into collected revenue"), enforced here by construction.
    ...

def expenses_total(period, basis="PAID") -> Decimal:
    # Sum of Expense.amount with status == basis (default PAID; APPROVED optionally selectable
    # for an "approved but not yet paid out" view) within the period.
    ...

def commissions_summary(period) -> dict:
    # {"earned": sum(status in EARNED,APPROVED,PAID), "approved": sum(status in APPROVED,PAID),
    #  "paid": sum(status == PAID), "reversed": sum(status == REVERSED, as a negative)}
    # within the period, by commission_ledger_entries.earned_at.
    ...

def net_cash_view(period) -> Decimal:
    # collected_revenue(period) - expenses_total(period, basis="PAID")
    #                           - commissions_summary(period)["paid"]
    # Deliberately does NOT subtract gross_invoiced or unpaid commissions -- "net cash" means real
    # cash movement only, not an accrual-basis profit figure (which this ledger does not claim to
    # produce -- see mini-financial-ledger-design.md's own "not a full accounting ERP" framing).
    ...
```

Every function above takes a `period` (start/end date, timezone-aware, matching the existing
`Asia/Amman`-anchored daily-snapshot convention — Milestone 15) and is pure/read-only — none of them
write anything, matching Reporting's dependency-graph position (Milestone 2: "no other context depends
on Reporting").
