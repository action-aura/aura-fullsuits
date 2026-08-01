# Phase 9.5C — Milestone 3: Employee Lifecycle CRM Impact

## Confirmed unchanged

`app/employees/services.py`'s suspension/termination/session-revocation
logic (Phase 9.5B) is not modified by Phase 9.5C. No new coupling was
introduced between the CRM layer and the employee-lifecycle state
machine beyond read-only checks.

## New read-only dependency: `employment_status` gates assignment targets

`assign_lead()`/`assign_customer()` both read `EmployeeProfile.
employment_status` (Lead) or `StaffUser.is_active`/`disabled_at`
(Customer) to decide whether a destination is a valid assignment target.
This is a **read**, not a write — suspending an employee through the
existing Phase 9.5B flow automatically makes them an invalid future
assignment target with zero CRM-side code change required, because the
check reads the live, authoritative field.

## Records remain historically attributed after suspension

A Lead/Customer already assigned to an employee who is later suspended
keeps that assignment untouched — `LeadAssignment`/`CustomerAssignment`
history is never rewritten by a suspension event, and the live
`assigned_employee_profile_id`/`assigned_sales_staff_id` pointer is not
cleared. This preserves real business history (who was working the
record when) even though that employee can no longer log in.

## Management indicator (not yet built — dashboard milestone)

"Records requiring reassignment" is a **query** over `Lead`/`Customer`
joined to `EmployeeProfile`/`StaffUser` where the assignee's status is
not `ACTIVE`/`is_active`. Deliberately not implemented as an automatic
mutation triggered by the suspension event — the spec is explicit that
"no automatic destructive reassignment" may occur. Tracked as part of
Milestone 14 (CRM dashboards), not this milestone.
