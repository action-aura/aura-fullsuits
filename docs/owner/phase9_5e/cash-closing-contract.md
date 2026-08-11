# Phase 9.5E — Daily Cash Closing Contract

Binding rules for `app/cash_closing/services.py`, per the governing spec's Authoritative Cash-Closing Procedure.

## Scope key

`(business_date, currency)` — no branch/operational-unit authority exists in this codebase, so per the spec's own fallback instruction, branch is omitted. Enforced at the DB level (`uq_cash_closing_scope`), not just in application code — `get_or_create_draft_closing()` returns the existing row rather than creating a second one for the same scope.

## Formula (server-authoritative, never caller-supplied)

```
expected_closing_cash = opening_cash
    + confirmed_cash_collections - confirmed_cash_refunds
    - cash_expense_payments - cash_commission_payouts
    + approved_cash_adjustments

variance = actual_counted_cash - expected_closing_cash
```

Only `CASH`-method, `CONFIRMED`/`PAID`/`RECORDED`-status transactions on the exact `business_date` are included — `recalculate_expected()` filters `PaymentRecord.status == "CONFIRMED"`, `CommercialRefund.status == "PAID"`, `ExpensePayment.status == "RECORDED"`, all further filtered to `payment_method`/`method` == `CASH`. Pending Payments, unconfirmed Refunds, and approved-but-unpaid Expenses are structurally excluded by these same status filters.

`cash_commission_payouts` required a real, additive schema extension: `CommissionPayoutBatch` (Phase 9.5D) had no payment-method concept at all. Added `payment_method` (nullable, migration `3f95d792998c`) — NULL (every pre-9.5E batch) is treated as "not cash," never overstating a cash outflow.

## Opening cash

The first closing for a scope requires an explicit `opening_cash_override` + `opening_cash_override_reason` (`OPENING_CASH_OVERRIDE_REQUIRES_REASON` otherwise). Every later closing derives its opening balance from the prior `CLOSED` closing's `actual_counted_cash` (falling back to `expected_closing_cash` if no count was ever recorded) — `_prior_closing()`.

## Lifecycle

`DRAFT → SUBMITTED → [REVIEW_REQUIRED] → APPROVED → CLOSED`, with `REOPENED` available from `APPROVED`/`CLOSED` and looping back through the cycle. Exact transition table in `app/expenses/errors.py::CASH_CLOSING_TRANSITIONS`. A variance exceeding `VARIANCE_MATERIAL_THRESHOLD` (50.00, in the closing's own currency) routes `SUBMITTED → REVIEW_REQUIRED` automatically inside `submit_closing()`.

## Segregation of duties

`decide_closing()` blocks `closing.prepared_by_staff_user_id == actor_staff_user_id` unconditionally — including `SUPER_ADMIN` (`test_super_admin_preparer_still_cannot_self_approve`), matching the exact reasoning of Expense approval's own Rule 2.

**AUDIT-032, Week 2 correctness pass**: `prepared_by_staff_user_id` is set once at DRAFT creation and never reassigned, but the `(business_date, currency)` scope is shared — any `cash_closing.prepare` holder may later `submit_closing()` a draft someone else created, supplying their own `actual_counted_cash`/`variance_explanation`. The original self-block only compared the approver against `prepared_by`, so a second preparer who submitted someone else's draft could approve their own submission — `prepared_by` still names the (uninvolved) original creator, never the person whose figures are actually being judged. Fixed by adding `submitted_by_staff_user_id` (nullable, migration `56216a528eff` — every pre-existing row predates the column and is unaffected), set in `submit_closing()`, and widening `decide_closing()`'s check to `actor_staff_user_id in (closing.prepared_by_staff_user_id, closing.submitted_by_staff_user_id)`. Both maker roles are now blocked from being the checker; a third, uninvolved account must always be the one to decide.

## Reopen

`reopen_closing()` requires a non-empty reason, `recent_auth_verified=True` (the service is request-context-free — the caller/route passes the result of `app.auth.session.has_recent_auth()`), and only fires from `APPROVED`/`CLOSED`. Every reopen writes an immutable `CashClosingReopenEvent` snapshotting the prior status/approver/expected/actual/variance **before** mutating the `CashClosing` row — so prior approval history survives a reopen as real rows, not inference.

## Late-transaction policy

`REOPEN_REQUIRED_FOR_SAME_BUSINESS_DATE` (the spec's own default): a transaction dated on an already-`CLOSED` business date can only be reflected by reopening that exact closing and recalculating (`recalculate_expected()` is idempotent and re-derives every aggregate fresh) — never by silently shifting it into a different day's numbers.
