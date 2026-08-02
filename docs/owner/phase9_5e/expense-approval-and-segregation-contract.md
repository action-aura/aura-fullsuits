# Phase 9.5E — Expense Approval & Segregation-of-Duties Contract

Binding rules for `app/expenses/approvals.py`, verbatim from the governing spec's Authoritative Expense Approval and Segregation Rules. This document exists so the code's own module docstrings have something real to point at — it does not restate the rules more loosely than the code enforces them.

## 1. Approval is not payment

`decide_expense_approval()` only ever sets `Expense.approved_amount`/`Expense.status = APPROVED` (or `REJECTED`/`RETURNED`). It never creates an `ExpensePayment`, never sets `PAID`/`PARTIALLY_PAID`, never touches `CashClosing`, never posts anything. Proven by `test_approval_never_creates_payment_or_sets_paid`.

## 2. Self-approval is always forbidden

Checked in `check_approver_eligibility()` regardless of the approver's other permissions — including `SUPER_ADMIN`. A permission grant is necessary but never sufficient. Proven by `test_requester_cannot_approve_own_expense` and `test_requester_with_super_admin_cannot_approve_own_expense`.

## 3. Beneficiary/payee conflict

The approver cannot be the expense's recorded `beneficiary_employee_profile_id` (populated only when the `Payee` is `payee_type=EMPLOYEE`). An `EXTERNAL` payee's creator/manager is never conflicted by that fact alone — `Payee.employee_profile_id` is NULL for `EXTERNAL` payees, so no conflict check ever fires for them. Proven by `test_employee_beneficiary_cannot_approve` and `test_external_payee_creator_not_automatically_conflicted`.

## 4. Approval fingerprint

`app/expenses/fingerprint.py` hashes: expense id, requester, beneficiary, payee, category, requested amount (quantized to the column's own precision), currency, expense date, business purpose (`Expense.description`), external reference, and the current sorted set of ACTIVE attachment content hashes. Recomputed live at decision time and compared to the value captured at submit time — never a caller-supplied version. Viewing, audit-record creation, and unrelated activity never appear in the payload, so they never invalidate a pending approval (`test_view_and_audit_actions_do_not_invalidate_approval`).

## 5. Approval amount

`approved_amount` must be `> 0` and `<= requested_amount`; a higher amount is rejected with `APPROVED_AMOUNT_EXCEEDS_REQUESTED` (`test_approved_amount_above_requested_rejected`). A lower amount is accepted (`test_approved_lower_amount_works`) and becomes `Expense.approved_amount`.

## 6. Resubmission

`RETURNED` is the only revise-and-resubmit state (`REJECTED` is terminal — see `operational-finance-funnel-contract.md`). `revise_expense()` only accepts a `RETURNED` expense; `submit_expense()` opens a brand-new `PENDING` `ExpenseApproval` row. The old row stays exactly as decided (`RETURNED`), immutable, and can never be decided again (`INVALID_EXPENSE_APPROVAL_TRANSITION` — `test_old_approval_cannot_be_reused_after_resubmission`).

## 7. Approver eligibility

`check_approver_eligibility()` checks, in order: account active, `expenses.approve` permission (or `is_super_admin`), an `EmployeeProfile` exists for the approver's `StaffUser`, that profile's `employment_status` is not `SUSPENDED`/`TERMINATED`, not the requester, not the beneficiary. Any Category/amount-threshold policy is a future extension point, not yet specified by the governing spec — not implemented until a concrete threshold policy is given.

## 8. Duplicate review is not approval

`app/expenses/duplicates.py`'s `override_duplicate_warning()` only writes an audit record with the caller-supplied reason — it never touches `Expense.status` or creates an `ExpenseApproval` decision. A separate call to `decide_expense_approval()` is always required afterward.
