# Phase 9.5D — Milestone 17: Commercial Dashboards Contract

`app/commercial_sales/dashboards.py` — two scopes, matching Milestone 16's RBAC/SoD model exactly. Unlike CRM (Phase 9.5C, which has an employee scope and a generic "management" scope open to any read-permission holder), commercial-sales/commission data is financially sensitive, so the aggregate view is scoped to `FINANCE` specifically — the only non-`SUPER_ADMIN` role that actually holds `commissions.view_all`/`refunds.approve`/`invoices.issue`.

## `employee_commercial_dashboard(actor_employee_profile_id, actor_staff_user_id) -> dict`

- `quotes_by_status`/`orders_by_status`/`invoices_by_status` — own-only, via `apply_ownership_filter()` (the same function extended incrementally through Milestones 5/8/9).
- `own_pending_approval_requests` — count of `CommercialApproval.status == "PENDING"` rows this employee's own actions requested (`requested_by_staff_user_id`), informational only: `SALES` never holds an approve permission (Milestone 16), so this is never an actionable queue for the employee viewing it, only a "here's what's still waiting on someone else" indicator.
- `commission_earned_unapproved`/`commission_approved_unpaid`/`commission_paid_total`/`commission_reversed_total` — `SUM(commission_amount)` filtered to `CommissionLedgerEntry.employee_profile_id == actor_employee_profile_id` and the relevant status. Never a peer's earnings — proven directly in `test_employee_dashboard_is_scoped_to_own_pipeline_and_commission` (a second employee's larger commission is asserted absent from the first employee's totals).

## `finance_commercial_dashboard() -> dict`

Cross-employee aggregate, no ownership filter (deliberately — the caller's authorization is the route-layer `commissions.view_all` permission check, Milestone 18/19's job to wire). Emphasizes actionable queues over vanity totals, matching the CRM management dashboard's `reassignment_required_leads` precedent:

- `quote_approvals_pending` — `CommercialApproval.status == "PENDING"`, all employees.
- `refunds_pending_approval` — `CommercialRefund.status == "DRAFT"`.
- `commissions_pending_approval` — `CommissionLedgerEntry.status == "EARNED"`.
- `commissions_approved_unpaid` — `CommissionLedgerEntry.status == "APPROVED"` (ready for a payout batch).
- `payout_batches_pending_approval` — `CommissionPayoutBatch.status == "DRAFT"`.
- `outstanding_invoice_total` — `SUM(total)` over `ISSUED`/`PARTIALLY_PAID` invoices (money still owed, a real FINANCE-relevant figure; never includes `DRAFT`/`VOID`/fully `PAID`/`REFUNDED`).

## What is deliberately absent

No per-employee revenue/commission leaderboard, no customer-level financial drill-down, no time-series/trend charts — none of these were named as required and each would be new surface area beyond "commercial dashboards" as a read-only operational summary. If a future milestone needs them, they compose from the same underlying tables without touching this contract.

## Test coverage

`tests/test_phase9_5d_dashboards.py` — 3 tests: employee-scope isolation (a peer's larger commission and invoice are provably absent from the viewing employee's own totals), finance-scope cross-employee aggregation, and the own-pending-approval-request count sourced from `add_quote_line()`'s real auto-created `CommercialApproval` row (Milestone 6), not a hand-constructed one.
