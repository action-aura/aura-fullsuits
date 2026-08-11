# Phase 9.5A Milestone 4 — Employee Account and Profile Model

## Reused, not duplicated

`StaffUser` (`owner/app/models/staff.py`) remains the one login table — email, password_hash, MFA,
sessions, roles, lockout (computed from `LoginAttempt`). No second login table.

## New: `EmployeeProfile` (`owner/app/models/employees.py`)

```
id (UUID PK), staff_user_id (FK -> owner_staff_users, UNIQUE — one profile per account),
employee_number (unique, e.g. "EMP-0001"), full_name, phone (nullable),
job_title (nullable), department (nullable),
employment_start_date (date), employment_end_date (date, nullable),
employment_status (PENDING | ACTIVE | SUSPENDED | TERMINATED | ARCHIVED, default PENDING),
manager_employee_profile_id (FK -> owner_employee_profiles, nullable, self-referential),
commission_plan_id (FK -> owner_commission_plans, nullable — Milestone 12's table, added once that
  migration exists; nullable so an employee can exist before a commission plan is assigned),
profile_image_reference (nullable, string — storage path/URL, not the image itself),
notes (text, nullable — management-only field, enforced at the API/serialization layer per role, not
  a separate table, since it's a single free-text field with no independent lifecycle),
created_at, updated_at, version (optimistic lock), archived_at (nullable),
created_by_staff_user_id, updated_by_staff_user_id
```

## Required behavior (design, real service logic in Milestone 22)

- One profile per `StaffUser` — DB-level `UNIQUE` on `staff_user_id`, not just an application check.
- `employee_number` unique — DB-level `UNIQUE`.
- Terminating an employee (`employment_status -> TERMINATED`) calls the existing real
  `StaffUser` session-revocation mechanism (`StaffSession.revoked_at`/`session_version` bump — the same
  real mechanism already used for `disable_staff()`), not a new one.
- Historical activity (leads, customer assignments, commission entries, audit rows) is never deleted or
  reassigned automatically on termination — ownership queries (Milestone 7) must still resolve a
  terminated employee's historical `assigned_*` foreign keys correctly; only *new* assignment to that
  employee is blocked going forward.
- Hard deletion is prohibited once any business activity exists (a lead, a commission entry, an audit
  row referencing this employee) — enforced by foreign-key `ON DELETE RESTRICT` (default, no cascade
  configured) on every table that references `employee_profile_id`, not by an application check alone.

## Employment status transitions (service-enforced, not a DB constraint)

```
PENDING -> ACTIVE -> SUSPENDED -> ACTIVE
                   -> TERMINATED -> ARCHIVED
ACTIVE -> TERMINATED -> ARCHIVED
```

`ARCHIVED` is terminal (matches the existing `Customer.lifecycle_status="ARCHIVED"` convention —
archived rows remain queryable for history, just excluded from active-employee lists by default).

## Explicitly not employee attendance

`employment_status` is an HR/administrative state, entirely separate from `EmployeePresence`
(Milestone 5) — an `ACTIVE` employee can be `OFFLINE` for days without that meaning anything about
their employment.
