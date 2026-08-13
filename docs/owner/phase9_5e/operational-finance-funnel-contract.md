# Phase 9.5E Milestone 2 — Canonical Operational-Finance Funnel & Metric Definitions

Binding definitions for every service/report built in this phase. Read-derived, never re-invented per milestone.

## Domain funnel

`Payee (EXTERNAL|EMPLOYEE) → Expense (DRAFT) → SUBMITTED → [RETURNED ⇄ SUBMITTED]* → APPROVED (fingerprint-bound decision, amount ≤ requested) → ExpensePayment(s) (PARTIALLY_PAID → PAID) | REJECTED/VOID (terminal)`

Approval never creates an `ExpensePayment`, never sets `PAID`, never touches Cash Closing or any journal — approval only raises `Expense.approved_amount` and flips status to `APPROVED`. Payment is a separate, later, maker-checker-eligible action.

## Expense status set (extends the 9.5A enum, additive)

`DRAFT, SUBMITTED, RETURNED, APPROVED, PARTIALLY_PAID, PAID, REJECTED, VOID`

- `RETURNED`: sent back for correction, not a terminal rejection — requester may revise and resubmit (→ `SUBMITTED`), starting a new approval cycle (new fingerprint capture).
- `REJECTED`: terminal decision by an approver, distinct from `RETURNED`. Per the authoritative approval rules, a rejected expense "may be revised only according to the lifecycle contract" — this phase's lifecycle contract treats `REJECTED` as terminal (no resubmission path) and `RETURNED` as the sole revise-and-resubmit state, since the rules explicitly separate "returned for correction" (M22 employee flow step 12) from a hard rejection. A rejected expense that the business still wants to pursue is entered as a new Expense, preserving the old one's immutable history.
- `PARTIALLY_PAID` / `PAID`: derived from summing valid (non-reversed) `ExpensePayment` rows against `approved_amount`, recalculated transactionally on every payment/reversal — never hand-set.

## Canonical financial formulas (binding, reused by every report/test in this phase)

```
expense_outstanding = approved_expense_amount - valid_nonreversed_expense_payments
expense_outstanding >= 0   (invariant; overpayment is structurally prevented, not just discouraged)

expected_closing_cash = opening_cash
    + confirmed_cash_collections
    - confirmed_cash_refunds
    - cash_expense_payments
    - cash_commission_payouts
    + approved_cash_adjustments

variance = actual_counted_cash - expected_closing_cash

net_operational_cash_movement = confirmed_payments - confirmed_refunds - paid_expenses - recorded_commission_payouts
```

`net_operational_cash_movement` never subtracts approved-but-unpaid Expenses or unconfirmed Payments — it is a cash-movement figure, not an accrual one, and is never labeled "profit" anywhere in code, UI, or reports (Expenses/Commercial Sales have no COGS/revenue-recognition concept; calling this "profit" would be a false financial claim).

## Currency handling

Every operational figure is computed and reported **per currency, never summed across currencies**. A report or snapshot covering multiple currencies present them as separate labeled totals, never a blended number.

## Reporting/Cash-Closing read boundary

Reporting and Cash Closing are **read-only consumers** of Commercial Sales (Payment/Invoice/Refund/Allocation), Commissions (Ledger), and Expenses (Expense/ExpenseCategory/ExpensePayment). Neither introduces a second source of truth for any figure that already has a canonical source, and — inherited from the 9.5A `DailyActivitySnapshot` rule — no table this phase introduces for reporting purposes may be the target of a foreign key from any other subsystem's table. Cash Closing links to a business date, never to a hard FK into the snapshot tables.

## Scope key used throughout (Cash Closing, Report Snapshots)

`(business_date | period_start+period_end, currency)` — no branch/operational-unit authority exists anywhere in this codebase today (single-location Owner platform), so per the authoritative Cash-Closing procedure's own fallback instruction, branch/unit is omitted rather than invented.
