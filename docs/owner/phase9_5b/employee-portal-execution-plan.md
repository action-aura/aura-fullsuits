# Phase 9.5B — Execution Plan

Condensed from the governing spec's 22 milestones into build order (dependencies first):

1. **Lifecycle + guards** (`owner/app/employees/services.py` extended): `activate_employee`,
   `reactivate_employee`, `archive_employee`; shared `_assert_not_last_usable_super_admin()` wired into
   `suspend_employee`/`terminate_employee` and into `owner/app/staff/services.py`'s existing
   `disable_staff`/`assign_roles`.
2. **Migration**: additive `StaffInvitation.employee_profile_draft` column only (one migration).
3. **Secure creation**: `create_employee_invitation()` service (wraps `create_invitation` + draft),
   `create_staff_from_invitation()` extended to also create `EmployeeProfile` transactionally.
4. **First login**: extend `accept_invitation_submit()` to require MFA enrollment before a full session
   when required; activate the profile on completion.
5. **Presence derivation**: `presence_state(session)` pure function + `list_employee_presence()` query
   helper.
6. **Web routes + templates**: `owner/app/employees/routes.py` (`/employees` management screens),
   `owner/app/employees/self_routes.py` or a `profile` blueprint (`/profile` self-service),
   `owner/app/employees/dashboard.py` + route (`/employees/dashboard`).
7. **Operations API**: `owner/app/api_operations/routes.py` at `/api/operations/v1` — `/me`, `/me/sessions`,
   `/presence/heartbeat`, `/employees*`, lifecycle actions, roles.
8. **Session management**: per-session list + single/all revoke, self and management variants.
9. **Audit wiring**: new `action_code`s for every lifecycle/security event this phase adds.
10. **Tests**: lifecycle, last-SUPER_ADMIN, onboarding transaction/rollback, MFA gate, presence thresholds,
    IDOR, two-admin sync, RBAC.
11. **Preflight extension**: employee-domain integrity checks appended to the existing preflight module.
12. **Final regression, gate matrix, decision doc, tag.**

Templates reuse `layout/base.html` verbatim (new nav links added, no new visual framework). No Arabic/RTL
this phase — see `phase9-5b-scope-and-boundaries.md`.
