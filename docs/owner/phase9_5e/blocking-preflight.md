# Phase 9.5E Milestone 25 — Complete Blocking Preflight

`_check_operational_finance_domain_integrity()` (added Milestone 19, extended this milestone) is wired into the same single `run_preflight()` / `flask commercial preflight` entry point every prior phase's checks already use — there is no second preflight command, no parallel gate.

## What this milestone added on top of the Milestone 19 base

- **Attachment integrity**: no orphan `ExpenseAttachment` (references a real `Expense`), no `content_type` outside `ALLOWED_CONTENT_TYPES`, no `storage_key` containing a traversal/absolute-path pattern (`..`, leading `/`, `\`).
- **Scheduler/report-snapshot integrity**: no duplicate canonical key (the real backstop for `uq_report_snapshot_canonical_key`), and — a genuinely distinct check — no report key with **more than one `PUBLISHED` row simultaneously** (a corruption `regenerate_snapshot()`'s status-flip step is supposed to prevent; two different `snapshot_version`s can each individually satisfy the unique constraint while still being a real business-logic violation).
- **Cash-closing uniqueness**: no `(business_date, currency)` scope with more than one `CashClosing` row (the real backstop for `uq_cash_closing_scope`).

Combined with the Milestone 19 base (canonical status/visibility enums for all six new models, no overpaid/self-approved/beneficiary-approved expense), the operational-finance section now contributes 14 real checks to the full preflight run.

## What the complete preflight already covered (pre-existing, unmodified, reused not duplicated)

`run_preflight()` also runs, unchanged: signing-key health/trust-anchor, `all_permissions_seeded`/`role_permissions_synced` (permission-assignment validation — genuinely caught a real gap this milestone, see below), license pepper, employee-domain integrity, i18n catalog configuration, CRM domain integrity, commercial-sales domain integrity, and the informational (non-blocking) Super-Admin-MFA check.

Route/template manifests, the hardcoded-string scanner, and translation-catalog completeness are **not** re-implemented inside the runtime preflight command — they remain their own dedicated, already-passing test files (`test_phase9_5b_r2_owner_wide_template_rendering.py`, `test_phase9_5b_r_hardcoded_strings.py`), extended with the `operations_ui` directory and the new no-fixture routes in Milestones 16/17/12. This matches Phase 9.5D's own precedent — `run_preflight()` validates live database/runtime state, static-analysis checks stay in the test suite that already gates every commit and the final regression.

## Real gap found and fixed this milestone

Running `flask commercial preflight` for real against `aura_owner_dev` (not just the isolated test suite) failed with `all_permissions_seeded: FAIL` and `role_permissions_synced:{SALES,FINANCE,VIEWER}: FAIL` — the Milestone 14 RBAC additions (`expenses.manage_payees`, `cash_closing.*`, `report_snapshots.*`, `management_notes.view/manage` grants) had never been applied to the dev database with `flask seed-rbac`, only ever exercised against the isolated per-test scratch/test databases. Fixed by running `flask seed-rbac` for real against `aura_owner_dev` (135 permissions, 5 roles). Re-ran the preflight: **exit code 0, 50 OK, 0 FAIL**, one informational (non-blocking) warning about synthetic local test accounts lacking MFA — expected and documented as such by the check's own message.

## Healthy-data and corrupted-data proof (both required by this milestone)

- `test_operational_finance_preflight_passes_on_healthy_data` — zero FAIL among the operational-finance checks on a clean database.
- `test_operational_finance_preflight_catches_invalid_expense_status` (Milestone 19) — a raw `UPDATE` bypassing the service layer, caught.
- `test_operational_finance_preflight_catches_corrupted_attachment_storage_key` (this milestone) — a directly-inserted `ExpenseAttachment` with a `../../../../etc/passwd` storage key, caught.
- `test_operational_finance_preflight_catches_duplicate_published_snapshot` (this milestone) — two different snapshot versions for the same report key both left `PUBLISHED`, caught.

Every corruption test asserts `result.ok is False` — the exact nonzero-exit condition `flask commercial preflight`'s CLI wrapper turns into a real process failure.
