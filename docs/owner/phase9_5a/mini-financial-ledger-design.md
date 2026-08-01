# Phase 9.5A Milestone 13 — Mini Financial Ledger Design

Not a full accounting ERP — a Commercial Operations Ledger foundation, explicitly bounded.

## New: `expense_categories` / `expenses` (`owner/app/models/expenses.py`)

```
expense_categories: id, category_code (unique), name, is_active

expenses: id, category_id (FK), amount (Numeric), currency, expense_date (date), description,
  payment_method (bounded string), payment_reference (nullable), entered_by_employee_profile_id (FK),
  approved_by_staff_user_id (FK, nullable), status (DRAFT|SUBMITTED|APPROVED|REJECTED|PAID|VOID,
  default DRAFT), attachment_reference (nullable string — storage path, not the file itself),
  reversal_of_expense_id (FK, nullable — a correction is a new row referencing the original, matching
  the same append-only-correction pattern used everywhere else in this phase, never an in-place edit
  of a SUBMITTED+ expense), created_at, updated_at, version
```

## Status transitions

```
DRAFT -> SUBMITTED -> APPROVED -> PAID
                    -> REJECTED (terminal)
(APPROVED or PAID) -> VOID (via a reversal_of_expense_id-linked correction row, never in-place)
```

## Financial dashboard metric definitions (exact, server-computed — see `financial-metric-definitions.md`)

Gross invoiced, collected revenue, expenses, commissions, net cash view — all defined precisely to
prevent the exact mistake the spec warns against ("do not merge unpaid invoices into collected
revenue").

## Explicitly not this phase

General ledger / chart-of-accounts / double-entry bookkeeping, payroll, statutory financial statements.
`expenses` is a flat operational record, not a GL posting.
