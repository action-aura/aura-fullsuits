# Phase 9.5B Milestone 10 — Role Assignment Security Report

## Reused, not duplicated

The employee-detail screen's "change role" form posts to the **existing, unchanged**
`POST /staff/<uuid:staff_id>/roles` route (`owner/app/staff/routes.py::update_roles`,
`require_permission("staff.assign_roles")` + `require_recent_auth`) — no parallel employee-specific role
editor was built. `assign_roles()` (`owner/app/staff/services.py`) already: blocks a non-Super-Admin from
granting `SUPER_ADMIN` (`SelfEscalationError`, pre-existing), bumps `session_version` and revokes every
session on any role change (pre-existing), and is now additionally checked against last-usable-SUPER_ADMIN
removal only where it can actually matter — see below.

## Real analysis: does role assignment need the last-SUPER_ADMIN guard?

Investigated and confirmed **no**: `owner/app/security/rbac.py::get_staff_permission_codes()` checks
`StaffUser.is_super_admin` (a direct boolean) **before** ever consulting role assignments — Super Admin
permission grant bypasses the role-assignment table entirely. No route anywhere flips `is_super_admin`
after account creation (confirmed by a full-codebase grep). Therefore `assign_roles()` changing
`role_codes` — even removing the cosmetic `SUPER_ADMIN` Role row — **cannot** actually revoke a Super
Admin's real permissions. The guard is correctly placed only where it has real effect:
`disable_staff()`, `suspend_employee()`, `terminate_employee()` (all of which touch `is_active`, the
real gate). Documented here rather than adding an inert guard call to `assign_roles()` that could create
false confidence that role changes are "protected" when the actual protection lives elsewhere.

## `employees.assign_role` permission

Seeded (Phase 9.5A Milestone 17), granted to no role below `SUPER_ADMIN` — confirmed by
`owner/tests/test_phase9_5b_rbac_hardening.py`. Not currently wired to a distinct route (the reused
`staff.assign_roles` permission gates the actual route) — recorded as a real, minor naming/wiring gap:
a future phase could either route `employees.assign_role` to the same handler or retire the duplicate
permission code; neither changes behavior today since both are SUPER_ADMIN-only in practice (SALES/
SUPPORT/FINANCE/VIEWER hold neither).
