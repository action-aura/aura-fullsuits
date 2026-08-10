# Phase 9.5E — Correction: `management_notes.manage` Must Stay SUPER_ADMIN-Only

Found by the Milestone 26 full five-module regression (`pytest owner/tests/
-q`), not by inspection: `tests/test_phase9_5a_rbac_restrictions.py::test_sensitive_permissions_not_granted_below_super_admin`
failed with `management_notes.manage must not be granted to FINANCE`.

## Root cause

Phase 9.5A's own `sensitive-action-control-matrix.md` and
`rbac-permission-matrix.md` explicitly and deliberately commit
`management_notes.manage` to SUPER_ADMIN only: *"Manage shared management
notes | `management_notes.manage` | SUPER_ADMIN only (matches 'management
notes' being inherently a management-only concept)"*. That commitment is
enforced by a regression test written the same phase
(`test_sensitive_permissions_not_granted_below_super_admin`, in the
`SUPER_ADMIN_ONLY_PERMISSIONS` tuple since commit `21be07b`, 2026-08-01 —
before Phase 9.5E existed).

Phase 9.5E's own Milestone 14 granted `management_notes.manage` to FINANCE
(`app/staff/seed_data.py`), reasoning from the *unassigned-permission* class
of gap Phase 9.5D M16/M18 had already fixed for `quotes.approve` /
`payments.create` (permissions pre-seeded in 9.5A but "granted to no role at
all"). That reasoning applies correctly to `reports.regenerate_daily` and
`expenses.manage_payees` (which had no prior SUPER_ADMIN-only commitment
attached to them) but does **not** apply to `management_notes.manage` --
Phase 9.5A did not merely leave it unassigned, it explicitly reasoned about
it and restricted it. Milestone 14 did not cross-check the pre-existing
`SUPER_ADMIN_ONLY_PERMISSIONS` list before writing the FINANCE grant, so the
conflict went unnoticed until the first complete, whole-suite regression run
(this repository's per-milestone runs throughout M15-M26 all scoped to
`tests/test_phase9_5e_*.py`, which never included the Phase 9.5A test file).

## Resolution

Per this phase's standing instruction never to reinterpret or modify a
prior phase's closure/security documents: the fix is on the Phase 9.5E side.
`app/staff/seed_data.py`'s FINANCE role no longer grants
`management_notes.manage` -- it keeps `management_notes.view` (reading
notes addressed to it, which is not the same authority as
creating/editing/archiving them). Three Phase 9.5E tests that had
(incorrectly) exercised note creation/assignment as FINANCE were updated to
use a Super Admin account instead, matching the actual, intended RBAC
model:

- `tests/test_phase9_5e_web_operations_ui.py::test_management_notes_web_flow`
- `tests/test_phase9_5e_api_expenses_and_operations.py::test_management_note_hidden_from_unauthorized_employee_via_api`
- `tests/test_phase9_5e_full_local_e2e.py::test_full_local_e2e_expense_to_closing_to_report_to_note`

`flask seed-rbac` was re-run against `aura_owner_dev` to remove the
now-revoked grant from the live dev database, and the complete blocking
preflight was re-executed to confirm `role_permissions_synced` reports OK
again (see `infrastructure-security-regression.md`, re-run after this fix).

## What did not change

The Management Notes *feature* itself is unaffected: FINANCE can still view
every note visible to it (`management_notes.view`, unchanged), and
SUPER_ADMIN retains full manage authority via its wildcard, exactly as
Phase 9.5A specified from the start. No route, template, or service-layer
code changed -- this was purely an RBAC seed-data grant and its test
coverage.
