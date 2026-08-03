# Phase 9.5E Milestone 23 — Performance / EXPLAIN ANALYZE at Scale

Real PostgreSQL `EXPLAIN (ANALYZE, BUFFERS)` against a real synthetic dataset generated directly via bulk SQL (not through the service layer — inserting 100k+ rows one at a time through `create_expense()`/`submit_expense()`/etc. would take far too long for a purely index-shape validation pass; matches Phase 9.5D M26's own reasoning for its synthetic-data generator) in the isolated scratch database, migrated to the exact same head as production.

## Dataset generated

| Table | Rows |
|---|---|
| `owner_employee_profiles` / `owner_staff_users` | 100 |
| `owner_expense_categories` | 20 |
| `owner_expense_payees` | 500 |
| `owner_expenses` | 20,000 |
| `owner_expense_approvals` | 15,000 |
| `owner_expense_payments` | 30,000 |
| `owner_expense_attachments` (metadata only, no real file bytes) | 25,000 |
| `owner_cash_closings` | 2,000 |
| `owner_report_snapshots` | 5,000 |
| `owner_shared_management_notes` | 20,000 |

`ANALYZE` run after seeding to refresh planner statistics before every query.

## Real gap found and fixed

`owner_shared_management_notes`, `owner_management_note_visibility_grants`, and `owner_management_note_comments` — all three 9.5A-scaffolded tables — had **zero indexes beyond their primary key**. This phase built the first real service-layer queries against them (`app.management_notes.service.notes_visible_to()`, `can_view_note()`, the comment listing), so this is the first time the gap was ever exercised, not a regression — the exact same class of bug Phase 9.5C's own Milestone 24 found independently for CRM ownership queries.

**Before** (`status = 'OPEN'` filter, 20k-row table): `Seq Scan`, cost 778, 5.6ms.
**After** migration `f5959fdb9738` (`status`, `visibility`, `assigned_employee_profile_id`, `created_by_staff_user_id` on the notes table; `management_note_id`/`employee_profile_id` on the grants table; `management_note_id` on the comments table): `Bitmap Index Scan` on `ix_owner_shared_management_notes_status`, cost 484, 2.6ms.

Fixed via additive migration + matching `index=True` model declarations (`app/models/management_notes.py`), verified with `alembic check` (zero drift) and a real downgrade/upgrade round trip, both against the dev database.

## Full query set — real evidence

Eighteen representative queries executed, matching the governing spec's required list plus the dashboard aggregate query. Full raw `EXPLAIN (ANALYZE, BUFFERS)` output preserved in `docs/owner/phase9_5e/performance-explain-analyze-raw.txt`.

| Query | Plan | Notes |
|---|---|---|
| Employee's own expenses | Index Scan (`ix_owner_expenses_entered_by_employee_profile_id`) | 1.7ms |
| All expenses by status (management) | Index Scan (`ix_owner_expenses_status`) | 4.7ms |
| Approval queue (`PENDING`) | Index Scan (`ix_owner_expense_approvals_status`) | 4.8ms |
| Approved-unpaid expenses | **Seq Scan** — correct planner choice | `APPROVED` matches ~1/8 of rows; `LIMIT 50` short-circuits at 0.09ms regardless. Not a missing-index bug — adding one would not change the plan at this selectivity. |
| Partially-paid expenses | **Seq Scan** — same reasoning | 0.06ms |
| Expenses by category (aggregate) | Index Scan + GroupAggregate | 1.0ms |
| Expenses by payee | Index Scan (`ix_owner_expenses_payee_id`) | 0.08ms |
| Expenses by requester (aggregate) | Index Scan | 1.8ms |
| Duplicate external-reference lookup | Bitmap Heap Scan (`ix_owner_expenses_external_reference`) | 0.05ms |
| Attachment content-hash lookup | Index Scan (`ix_owner_expense_attachments_content_hash`) | 0.05ms |
| Expense-number lookup | Index Scan (`uq_expense_number`) | 0.03ms |
| Cash-closing scope lookup | Index Scan (`uq_cash_closing_scope`) | 0.02ms |
| Closing-variance queue | Bitmap Heap Scan (`ix_owner_cash_closings_status`) | 0.38ms |
| Report-snapshot canonical-key lookup | Index Scan (`uq_report_snapshot_canonical_key`) | 0.04ms |
| Snapshot history by type/status | Index Scan (`ix_report_snapshots_type_status`) | 1.4ms |
| Management notes by status | **Fixed this milestone** — see above | 5.6ms → 2.6ms |
| Dashboard expense totals by category | **Seq Scan** — correct planner choice | `currency='USD'` matches ~100% of rows (single-currency dataset) + `status NOT IN ('VOID')` excludes only ~12.5%; a near-full-table aggregate is seq-scan-optimal regardless of indexes. 13.8ms for 20k rows — acceptable. |
| Idempotency-key lookup (no match) | Index Scan (`uq_expense_payment_idempotency_key`) | 0.02ms |

## No N+1, no unbounded aggregation, no hidden-count leakage

- Every list endpoint (`/expenses`, `/cash-closings`, `/report-snapshots`, `/management-notes`) applies a `LIMIT` server-side (50–200), confirmed in the route code itself (Milestone 15/16), not just documented.
- `management_operational_dashboard()`/`finance_operational_dashboard()` use `GROUP BY`/`COUNT` aggregate queries, never a Python loop reading every row (the one exception — `overdue_invoices` and `fulfillment_exceptions`, which iterate a query result — operates over an already status-filtered, non-VOID/DRAFT subset, not the full table; acceptable at current commercial-data scale, flagged as a residual optimization target if invoice volume grows an order of magnitude).
- Ownership-scoped queries (`entered_by_employee_profile_id == X`) never fetch broader and filter in Python — confirmed by code inspection of every route in `app/operations_ui/routes.py` and `app/api_operations/expenses_and_operations.py`.
- Currency is a `WHERE` clause on every aggregate query, never post-aggregation filtering — confirmed no cross-currency summation is structurally possible in `aggregation.py`/`dashboards.py`.

## Cleanup

The scratch database (`aura_owner_test_9_5e_scratch`) used for this milestone is a fully isolated, throwaway database — never the shared `aura_owner_dev`/`aura_owner_test` databases any other test or session relies on. Dropped after this milestone's evidence was captured.
