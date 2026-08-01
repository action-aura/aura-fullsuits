# Phase 9.5B Milestone 6 — Employee Detail UI Contract (real code)

`GET /employees/<uuid>` (`require_permission("employees.view_all")`). Sections implemented, real data
source for each:

1. **Overview** — `EmployeeProfile` fields directly, `profile.manager.full_name` via the real
   self-referential relationship, presence via `employee_presence_state()`.
2. **Account security** — `StaffUser.email`/`is_active`/`disabled_at`/`disabled_reason`/`mfa_credential`/
   `mfa_required`/`last_login_at`; active-session count from `list_sessions_for_staff()`.
3. **Roles and permissions** — reuses the **existing, unchanged** `staff.update_roles` route/form
   (Milestone 10 decision — see `role-assignment-security-report.md`; no parallel role editor built).
4. **Sessions** — `list_sessions_for_staff()`, single/all revoke via the real
   `revoke_session_by_id()`/`revoke_all_sessions_for_staff()` (Milestone 11).
5. **Audit timeline** — direct `AuditLog` query filtered on `entity_type="employee_profile"` +
   `entity_public_id=str(profile.id)`, the exact same append-only table every other Owner audit screen
   reads (no new audit storage).
6. **Lifecycle actions** — buttons conditionally rendered per `profile.employment_status` (only a
   currently-valid transition's button ever renders — matches `ALLOWED_TRANSITIONS`, Milestone 2), each
   posting to the corresponding route, each requiring `require_recent_auth` for suspend/terminate.
7. **Edit profile** — `full_name`/`job_title`/`department`/`manager_employee_profile_id` only (employee
   number, employment status, employment start date, commission plan are **not** in this form — matching
   the spec's own "management may edit; certain fields still require the dedicated lifecycle/role actions,
   not a generic edit" posture. A manager-cycle check (`_creates_manager_cycle()`) runs before any manager
   reassignment is applied. Optimistic-lock `version` is a hidden field; a stale submission is rejected
   with `VERSION_CONFLICT` rather than silently overwriting a concurrent change (Non-Negotiable
   Principle 12).

Not built this phase (real, documented gap, not silently missing): a dedicated commission-plan-assignment
UI on this screen (the field exists and is set at creation time; changing it later has no route yet —
`employees.manage_commission_plan` permission is seeded and ready for a future phase's route).

Real, tested proof: `owner/tests/test_phase9_5b_web_routes.py::test_management_can_create_list_and_view_employee`
(full create → accept → detail flow, asserts the employee's real name renders).
