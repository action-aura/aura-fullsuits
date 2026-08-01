# Phase 9.5B Milestone 3 — Employee Onboarding Security Contract (real code)

## `create_employee_invitation()` (`owner/app/staff/services.py`)

Wraps the existing, real `create_invitation()` (unchanged) with an employee-profile draft, stored as
`StaffInvitation.employee_profile_draft` (JSON, additive column). Validates before creating anything
(`_validate_employee_draft`): `employee_number`/`full_name`/`employment_start_date` required,
`employee_number` unique against both materialized `EmployeeProfile` rows AND every still-open
invitation's own draft (real bug found and fixed during this milestone — see below), `manager_employee_
profile_id`/`commission_plan_id` must reference real rows when supplied. Defaults `mfa_required=True`.

## Real bug found and fixed this milestone

First test run: two invitations created back-to-back with the same `employee_number` both succeeded,
because the uniqueness check only queried materialized `EmployeeProfile` rows — but a profile is only
materialized at *acceptance*, not at invitation-creation time. Two concurrent invitations could reserve
the same employee number and race. **Fixed** by also checking every open (`accepted_at IS NULL`,
`revoked_at IS NULL`, `expires_at > now()`) invitation's own JSON draft for a colliding `employee_number`.

## `create_staff_from_invitation()` (extended, not replaced)

One transaction: `StaffUser` + role assignments + (when a draft is present) `EmployeeProfile`, one
`db_session.commit()` at the end. Any exception before that point (e.g. a real DB-level UNIQUE violation
that slipped past the fail-fast check above) rolls back the whole thing — no orphaned `StaffUser` without
its `EmployeeProfile`, matching Non-Negotiable Principle 3 exactly. `staff.mfa_required` is set from the
draft's `mfa_required` flag as part of the same transaction.

## No plaintext password, no logged/audited setup secret

Unchanged from the pre-existing `StaffInvitation` mechanism (Phase 5): `generate_token()` (256 bits),
`hash_token()` (SHA-256) at rest, single-use (`accepted_at`), expiring (3-day TTL, unchanged), revocable,
displayed once (`staff/invitation_created.html`), never appears in `audit_record()`'s payload (only
`email`/`role_codes` are recorded — the new `employee_profile_draft` is likewise never included in the
`STAFF_INVITATION_CREATED` audit event's `after_state`, confirmed by reading the unchanged
`create_invitation()` body).

## Setup token invalidated by suspension/termination

Not a new mechanism: `_find_valid_invitation()` already rejects on `accepted_at`/`revoked_at`/
`expires_at` — an invitation for an employee who is suspended/terminated before ever accepting is a
real edge case with no dedicated new check needed, because a not-yet-accepted invitation has no
`EmployeeProfile` to suspend/terminate in the first place (the profile does not exist until acceptance).
