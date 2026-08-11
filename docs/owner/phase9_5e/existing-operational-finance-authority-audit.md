# Phase 9.5E Milestone 1 — Existing Operational-Finance/Reporting Authority Audit

Audit of everything Phase 9.5A–9.5D already built that Phase 9.5E's scope (Expenses, Cash Control, Daily Closing, Executive Reporting, Scheduled Snapshots, Management Collaboration) could touch. Read from the actual current code on this branch (tag `aura-owner-commercial-sales-phase9-5d-complete`), not from memory of what the specs originally said would be built.

## 1. Expenses — schema exists (9.5A M13), zero service/route/UI layer

`owner/app/models/expenses.py`: `ExpenseCategory` (code, name, active flag) and `Expense` (category FK, amount/currency, expense_date, description, payment_method, payment_reference, `entered_by_employee_profile_id`, `approved_by_staff_user_id`, `status` in `DRAFT/SUBMITTED/APPROVED/REJECTED/PAID/VOID`, `attachment_reference` — a bare `String(512)`, not a real attachment entity, `reversal_of_expense_id` for append-only correction, `version`). Table created by migration `3c0d51d82d8c` (9.5A), applied (`alembic current` = `b2f6a8e13c74`, downstream of it). Explicitly documented in the model docstring as "not a full accounting ERP — a flat operational expense record, never a GL posting" (`docs/owner/phase9_5a/mini-financial-ledger-design.md`).

**No service module exists** (`owner/app/expenses/` does not exist). No route, no UI, no attachment storage, no lifecycle-transition validation, no duplicate/fraud-signal detection — all of Phase 9.5E's own scope, genuinely unbuilt.

RBAC is already fully pre-seeded and role-assigned (`owner/app/staff/seed_data.py`): `expenses.create/view_own/view_all/approve/pay/void` exist; SALES has `create`+`view_own`, FINANCE has `view_all/approve/pay/void`. This matches the maker-checker shape used throughout Commercial Sales (creator ≠ approver) and can be reused as-is — no new permission design needed for the base expense lifecycle.

## 2. Management Collaboration Notes — schema exists (9.5A M14), zero service/route/UI layer

`owner/app/models/management_notes.py`: `SharedManagementNote` (title/body/category/priority, `status` in `OPEN/IN_PROGRESS/DONE/ARCHIVED`, `pinned`, `created_by_staff_user_id`, `updated_by_staff_user_id`, `assigned_employee_profile_id`, `visibility` in `MANAGEMENT_ONLY/SPECIFIC_EMPLOYEES/ALL_STAFF`, `due_date`, `version`, `archived_at`), `ManagementNoteVisibilityGrant` (only populated when visibility is `SPECIFIC_EMPLOYEES`), `ManagementNoteComment`. Genuinely separate from the customer-scoped `CustomerNote` (Phase 9.5C) per its own docstring — no overlap.

**No service module exists.** RBAC permissions (`management_notes.view`, `management_notes.manage`) are defined but **granted to no role at all** — the same class of real gap found and closed in Phase 9.5D M16 (`quotes.approve`/`orders.approve`) and M18 (`payments.create`): a permission pre-seeded in 9.5A and never wired to a concrete role.

## 3. Daily Reporting — schema + structure-proof exists (9.5A M15/M22), zero real aggregation

`owner/app/models/daily_reports.py`: `DailyActivitySnapshot` (`business_date` unique, timezone, `generation_status` in `PENDING/GENERATING/COMPLETE/FAILED`, `generated_by` in `SCHEDULER/MANUAL`, `source_range_start/end`, `metric_payload` JSONB, `schema_version`, `failure_reason`, `rerun_count`). Explicit, load-bearing architectural rule in the model's own docstring: **"No foreign key into any other table — Reporting reads everything, nothing reads it back"** (`docs/owner/phase9_5a/domain-dependency-rules.md`). This is a hard constraint Phase 9.5E's Executive Reporting/Scheduled Snapshots milestones must continue to honor — no other subsystem may ever gain a dependency on the reporting tables.

`owner/app/daily_reports/services.py` exists but is explicitly scoped (9.5A M22) to `build_synthetic_snapshot_structure()` — a **zero-valued, schema-proving function only**, never persisted, real DB aggregation (`new_leads` count, `confirmed_payments` sum, `commissions_earned`, `expenses` total, `security_events`, etc. — the full metric catalog already defined) explicitly deferred, by that milestone's own docstring, to "the real `DailySnapshotService.generate()` job... not built here, not faked here." This is precisely Phase 9.5E's Daily Closing / Executive Reporting scope — the shape is already specified and validated, the real implementation is not.

RBAC: `dashboard.view_own/view_all` and `reports.view_own/view_all/regenerate_daily` are pre-seeded; `view_own`/`view_all` are already role-assigned (SALES: `view_own`; FINANCE/VIEWER: `view_all`); `reports.regenerate_daily` is defined but **granted to no role** — another pre-existing unwired gap.

## 4. Not present at all — genuinely new for Phase 9.5E

- **Payee** entity (Expenses currently only has a free-text `payment_reference`, no first-class Payee/Vendor record, no payee-level duplicate-detection or history).
- **Secure attachment** infrastructure of any kind — no attachment/file model, no upload route, no storage helper (`grep` across `app/` found nothing; `Expense.attachment_reference` is a bare string column, not a storage-backed entity).
- **Cash closing / operational cash control** — no model, no service, nothing.
- **Scheduled report snapshot** infrastructure — no scheduler dependency in the project at all (no APScheduler, no Celery in `requirements*.txt`); `DailyActivitySnapshot.generated_by = SCHEDULER` is a value the enum anticipates, but nothing currently produces it.
- **Executive/role-scoped dashboards** beyond the existing Commercial Sales dashboards (Phase 9.5D `app/commercial_sales/dashboards.py`, scoped to commercial data only) and the bare `dashboard.view_own/view_all` permission shape — no cross-domain executive view exists yet.

## Migration/DB state

Single 9.5A migration `3c0d51d82d8c_phase_9_5a_commercial_operations_...py` created all of `owner_expense_categories`, `owner_expenses`, `owner_shared_management_notes`, `owner_management_note_visibility_grants`, `owner_management_note_comments`, `owner_daily_activity_snapshots`. Dev DB is current (`alembic current` = `b2f6a8e13c74`, the Phase 9.5D head, downstream of the 9.5A migration). No schema drift found for these tables in this audit.
