# Phase 9.5B Milestone 2 — Employee Lifecycle Contract (real code)

## Transitions (`owner/app/employees/services.py::ALLOWED_TRANSITIONS`)

```
PENDING    -> ACTIVE, SUSPENDED, TERMINATED
ACTIVE     -> SUSPENDED, TERMINATED
SUSPENDED  -> ACTIVE, TERMINATED
TERMINATED -> ARCHIVED
ARCHIVED   -> (terminal)
```

`TERMINATED -> ACTIVE` is absent (no rehire policy this phase, per the governing spec's explicit
instruction). Every transition function calls `_assert_valid_transition()` first and raises
`InvalidEmploymentTransitionError` for anything not in the map — enforced in the service layer, the one
place every route/API caller must go through (no direct `profile.employment_status = ...` assignment
exists anywhere outside this module).

## Real gap found and fixed this milestone: suspension/termination did not block new login

Phase 9.5A's `suspend_employee()`/`terminate_employee()` revoked existing sessions
(`revoke_all_sessions_for_staff()`) but never touched `StaffUser.is_active` — and
`app.auth.services.authenticate()` gates login **only** on `is_active`/`disabled_at`, with zero knowledge
of `EmployeeProfile.employment_status`. A suspended/terminated employee could log back in immediately
after their sessions were revoked and get a fresh one. **Fixed**: `suspend_employee()`/
`terminate_employee()` now also set `StaffUser.is_active = False` — reusing the exact existing gate
`authenticate()` already checks, not a new one. `reactivate_employee()`/`activate_employee()` restore
`is_active = True`, unless the account was *also* independently disabled via the separate
`app.staff.services.disable_staff()` action (`StaffUser.disabled_at` set) — that is a distinct admin
decision this phase does not silently override; the profile still reactivates, but the account stays
login-blocked until the separate disable is explicitly reversed. `account_login_restored` is recorded in
the audit payload so this distinction is visible after the fact.

## Real gap found and fixed this milestone: no last-usable-SUPER_ADMIN protection

See `owner/app/security/super_admin_guard.py` — `assert_can_remove_super_admin_status()` is called from
`suspend_employee()`, `terminate_employee()`, and `app.staff.services.disable_staff()` (the pre-existing
generic account-disable action, hardened by this same phase). Not called from `assign_roles()`: confirmed
by reading `owner/app/security/rbac.py::get_staff_permission_codes()` that `StaffUser.is_super_admin` (a
direct boolean) is the real authority for Super Admin permissions — it bypasses role-assignment lookup
entirely — so changing `role_codes` via `assign_roles()` cannot actually remove Super Admin status; no
route exists anywhere that flips `is_super_admin` after account creation, so the guard's real, exploitable
surface is exactly the three call sites above.

## Presence is not consulted by the state machine

`employment_status` and `EmployeePresenceSession`-derived presence remain fully independent, per
`employee-presence-contract.md`'s own explicit rule — an `ACTIVE` employee can be `OFFLINE` for days,
and suspending an employee does not touch any presence row (the next heartbeat attempt simply gets
rejected because the session was revoked and `StaffUser.is_active` is now `False` — see
`presence-implementation-report.md`).

## Hard-delete prohibition

Unchanged from Phase 9.5A: every table referencing `employee_profile_id` uses the SQLAlchemy/Postgres
default `ON DELETE RESTRICT` (no cascade configured) — a profile with any real business activity cannot
be hard-deleted at the database level, regardless of application code. `archive_employee()` is the only
terminal action, and it never deletes the row.
