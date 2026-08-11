# Phase 9.5B — Account/Profile State Reconciliation

Two independent state surfaces, reconciled by the service layer, never by the caller:

| `EmployeeProfile.employment_status` | `StaffUser.is_active` | `StaffUser.disabled_at` | Can log in? |
|---|---|---|---|
| PENDING (pre-setup) | `True` (default) | `NULL` | Only via the one-time setup link, not a normal login (no password set yet) |
| ACTIVE | `True` | `NULL` | Yes |
| SUSPENDED | `False` (set by `suspend_employee`) | `NULL` | No |
| TERMINATED | `False` (set by `terminate_employee`) | `NULL` | No |
| ARCHIVED | `False` (inherited from TERMINATED) | `NULL` | No |
| any | `False` | set (independent `disable_staff()` action) | No, regardless of employment_status |

`disabled_at`/`disabled_reason` remain exclusively written by `app.staff.services.disable_staff()` (the
pre-existing, generic "disable this account" action) — the employee-lifecycle functions never set or
clear them, keeping the two admin decisions (employment status vs. generic account disable) independently
auditable and independently reversible. `reactivate_employee()` only restores `is_active=True` when
`disabled_at IS NULL`; otherwise the profile-level state changes to ACTIVE but the account stays
login-blocked, and `EMPLOYEE_REACTIVATED`'s audit payload records `account_login_restored: false` so this
is never silently ambiguous.
