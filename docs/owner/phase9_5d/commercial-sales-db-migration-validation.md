# Phase 9.5D — Milestone 22: DB/Migrations/Indexes/Numbering Validation

Consolidated structural pass over everything Milestones 3–19 built incrementally, per the execution plan ("migrations land incrementally per milestone, this is the final consolidated validation pass").

## Migration chain integrity

Single linear alembic head (`a3c8e5d29f47` before this milestone's own addition, `b2f6a8e13c74` after) — no branching. Full downgrade→upgrade round trip exercised against the real dev DB across all 6 Phase 9.5D migrations (`b7e4a2c91f30` → `c92d5f18a4e6` → `d15e6a3b7c92` → `e4a8c1f6b0d3` → `f2b7d4e91a63` → `a3c8e5d29f47`, plus this milestone's own `b2f6a8e13c74`): downgraded one step past the first, then upgraded back to head — clean both directions, no errors, no manual reconciliation needed (unlike Milestone 5/7's mid-development constraint-rename incidents, which required raw-SQL fixes at the time).

## Real gap found and closed: missing FK indexes on hot query paths

The same class of bug Phase 9.5C's own Milestone 24 found ("a repo-wide missing-index gap across every CRM ownership/child-lookup query"), found here by auditing every FK column that appears in a real `.where(X == ...)` clause across `app/commercial_sales/*.py`, `app/commissions/*.py`, and their API/web routes (25 real usage sites grepped, not guessed). 12 columns were missing an index despite being queried on every page load:

- `Quote`/`SalesOrder`/`CommercialInvoice`.`created_by_employee_profile_id` — `apply_ownership_filter()` runs this comparison on every list/detail page for every non-`*_all`-permission actor.
- `QuoteLine.quote_id`, `SalesOrderLine.sales_order_id`, `CommercialInvoiceItem.commercial_invoice_id` — every document-detail page's line lookup.
- `SalesOrder.quote_id`, `CommercialInvoice.sales_order_id` — the "does an Order/Invoice already exist for this Quote/Order" idempotency checks in `create_order_from_quote()`/`create_invoice_from_order()`, plus the web UI's order-detail invoice lookup.
- `CommercialApproval.requested_by_staff_user_id` — the Milestone 17 employee dashboard's `own_pending_approval_requests` query.
- `CommissionLedgerEntry.employee_profile_id`/`source_commercial_invoice_id` — the Milestone 17 dashboards and `reverse_commissions_for_refund()`.
- `EmployeeCommissionPlanAssignment.employee_profile_id` — `resolve_active_rule()`, called on every single payment allocation (the real commission-earning trigger).

`PaymentAllocation.commercial_invoice_id`/`.payment_record_id` (Milestone 11) and `CommissionLedgerEntry.source_payment_allocation_id` (Milestone 15) were already correctly indexed by their own originating milestones — confirmed, not touched.

One real naming collision found while writing the migration: `EmployeeCommissionPlanAssignment`'s plain `index=True` would auto-generate a 67-character index name (`ix_owner_employee_commission_plan_assignments_employee_profile_id`), exceeding Postgres's 63-character identifier limit — `alembic upgrade` failed outright with `IdentifierError` on first attempt. Fixed with an explicit shorter name (`ix_owner_commission_plan_assignment_employee_profile_id`) declared via `__table_args__` on the model, kept in sync with the migration's own literal name (SQLAlchemy's implicit `index=True` naming and the migration's explicit `op.create_index()` name must match exactly or `test_no_schema_drift_between_models_and_migration` fails — this is precisely how the mismatch would have been caught even if the length error hadn't surfaced it first).

Migration `b2f6a8e13c74` applied to the dev DB; the test DB is provisioned fresh per test run by the existing fixture chain (no separate seed step needed here, unlike RBAC's `flask seed-rbac` which is idempotent-but-manual).

## Document numbering

`app/commercial_sales/numbering.py::allocate_document_number()` (Milestone 5) — unchanged this milestone, already proven concurrency-safe via a 12-thread race test (`test_phase9_5d_numbering.py`) using real `SELECT ... FOR UPDATE` + `SAVEPOINT`. Re-verified as part of the full regression, not re-tested independently here.

## Verification

Full downgrade/upgrade round trip (manual, against dev DB) → `test_no_schema_drift_between_models_and_migration`/`test_no_schema_drift_after_phase6` (2/2, fresh run) → full Owner regression (865/865, includes the schema-drift tests as part of the normal suite, confirms no stale-state false negative). An earlier standalone schema-drift run had failed with "Detected removed index" for all 12 new indexes — traced to having been launched *before* the index migration existed in this session's timeline (a background command started earlier, completing later); re-run fresh against the current code and DB state, confirmed clean. Documented here as a reminder that a background task's completion notification proves it *finished*, not that it ran against the *current* state — always re-verify a stale-looking failure before treating it as real.
