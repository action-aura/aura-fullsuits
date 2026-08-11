# Phase 9.5B Milestone 17 — Management Synchronization Evidence

Two synthetic `SUPER_ADMIN` accounts (never hardcoded to real names — RBAC is account-independent,
matching Phase 9.5A's own `duplication-risk-report.md` precedent), 7 real tests
(`owner/tests/test_phase9_5b_management_sync.py`):

1. Both admins' `list_employees()` calls return identical rows (same underlying table, no per-actor
   filtering for a role holding `employees.view_all` via the SUPER_ADMIN wildcard).
2. An employee created by admin A is immediately visible to admin B's own next query (same DB commit,
   no cache, no per-session materialized view).
3. A profile field admin A sets is what admin B reads next (`find_own_profile()`).
4. Optimistic locking: `EmployeeProfile.version` increments on every write; a second writer holding a
   stale version is rejected with `VERSION_CONFLICT` at the real API layer (proven directly in
   `test_phase9_5b_operations_api.py::test_update_employee_version_conflict` — this file confirms the
   version actually changes so a concurrent B *would* be stale).
5. Admin A suspending an employee is visible to admin B's next read, and the `EMPLOYEE_SUSPENDED` audit
   row's `actor_staff_user_id` is A's real ID — never B's, never a shared/generic identity.
6. Sessions and MFA secrets are fully independent per account (A logging in creates zero session rows for
   B; A's and B's encrypted TOTP secrets differ).
7. Last-usable-SUPER_ADMIN protection holds correctly with two *real* admins present: disabling one
   succeeds (one remains), disabling the second (now the last) is blocked.

No shared admin username exists anywhere in this design — `StaffUser.email` is unique per account
(DB-level `UNIQUE`), and nothing in the RBAC/permission model ever branches on a person's name.
