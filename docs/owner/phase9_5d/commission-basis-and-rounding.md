# Phase 9.5D — Commission Basis and Rounding

## Basis — confirmed, allocated payment, per the governing Non-Negotiable rules

Commission is earned only from **confirmed Payment Allocation** (Milestone 15's real posting trigger — wired to `payments.py::confirm_payment()`/`allocation.py::allocate_payment()`). Never from:

- Quote total or acceptance.
- Sales Order total or confirmation.
- Commercial Invoice total or issuance.
- An unconfirmed (`PENDING`) Payment.
- Tax, unless a future rule type explicitly opts in (not implemented this phase — `calculate_commission()`'s formula never references `CommercialInvoice.tax_total`).
- A refunded or cancelled transaction (reversed via Milestone 15's append-only reversal entries, never by editing the original).

This basis choice is real and load-bearing, not incidental: `PaymentAllocation.allocated_amount` is the only value in the entire commercial-sales schema that represents money genuinely, confirmedly collected against a specific invoice — everything upstream (Quote/Order/Invoice totals) is a claim about what *should* be collected, not proof that it *was*.

## Rounding — reused, not reinvented

`calculate_commission()` (Phase 9.5A, unchanged) already uses `ROUND_HALF_UP` at 2 decimal places — the same codebase-wide convention `app/commercial_sales/calculator.py` (Milestone 3) independently confirmed and reused. No new rounding policy is introduced by Milestone 14/15.

## Proportional earning on partial allocations (forward reference to Milestone 15)

Since the basis is `PaymentAllocation.allocated_amount` (not the full `PaymentRecord.amount` or the full `CommercialInvoice.total`), a partial allocation naturally produces a proportionally smaller commission — no special-case "partial commission" logic is needed; `calculate_commission(rule, allocation.allocated_amount)` is simply called with a smaller `base_amount`. Two partial allocations against the same invoice (from the same or different Payments) each independently trigger their own commission-earning evaluation, on their own allocated amount — matching the Non-Negotiable rule "partial allocations create proportional earnings" without any additional arithmetic beyond what `calculate_commission()` already does.

## Historical rate immutability

`CommissionRuleVersion` is append-only (Milestone 14); a `CommissionLedgerEntry` (Milestone 15) pins the exact `commission_rule_version_id` it was computed against, permanently. A later rate change (a new `CommissionRuleVersion` row) never recomputes or retroactively alters an already-earned entry — the entry's own stored `commission_amount` is the permanent record of what was actually paid out at the rate that was actually in effect at earning time.
