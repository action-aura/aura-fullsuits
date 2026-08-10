# Phase 9.5B — Employee Duplication Risk Report

## Setup/invitation token

**Risk**: building a second "employee setup token" table/flow alongside `StaffInvitation`.
**Resolution**: `StaffInvitation` extended additively (`employee_profile_draft` column) and reused as the
one real setup mechanism — see `phase9-5a-evidence-reuse-decision.md`.

## Session revocation

**Risk**: a new "employee session kill" function alongside `revoke_all_sessions_for_staff`/
`revoke_session`. **Resolution**: every lifecycle action (suspend/terminate) and every management
session-revoke route calls the exact existing functions in `owner/app/auth/session.py` — zero new
revocation logic.

## Role/permission management

**Risk**: a new "employee role editor" duplicating `owner/app/staff/routes.py::update_roles` +
`assign_roles()`. **Resolution**: the employee-detail screen's "change role" action posts to the
**existing** `/staff/<uuid:staff_id>/roles` route (now guarded with the new last-SUPER_ADMIN check added
in `assign_roles()` itself, benefiting every existing caller too) — no parallel role-assignment code path.

## Presence vs. audit

**Risk**: logging every heartbeat as an audit event (Phase 9.5A's `employee-presence-contract.md`
explicitly rejected this — "would dilute the audit log's real security/financial signal value").
**Resolution**: heartbeats write only to `EmployeePresenceSession` (already real), never call
`audit_record()`. Confirmed no `PRESENCE_HEARTBEAT` action code exists or is added.

## Dashboard

**Risk**: a second dashboard framework alongside `owner/app/dashboard/` (existing, Phase 5).
**Resolution**: employee metrics live in a new `owner/app/employees/dashboard.py` service function,
rendered by a new template reusing the exact same `layout/base.html` and `.card`/badge CSS classes as
the existing dashboard — not a new visual system, not merged into the existing single-page dashboard
(which is out of this phase's scope to redesign), but consistent with it.

## Employee list/detail vs. existing `/staff` screens

**Risk**: `owner/app/staff/routes.py` already has `/staff` (list) and `/staff/<uuid>` (detail) — building
`/employees` alongside it could look like an unexplained duplicate.
**Resolution**: genuinely different data. `/staff` shows `StaffUser` (auth accounts — every account,
including ones with no employee profile yet, e.g. a freshly-accepted-but-not-yet-profiled account from
before this phase). `/employees` shows `EmployeeProfile` (HR data — full name, employee number, job
title, department, manager, employment status, presence) joined to its `StaffUser` for security fields.
Both remain real and useful; `/employees` becomes the primary screen the spec asks for, `/staff` remains
for raw-account administration (e.g. an account that intentionally has no profile). No table, model, or
service function is duplicated between them — `/employees` queries `EmployeeProfile` first and joins
`StaffUser`, `/staff` queries `StaffUser` and has no `EmployeeProfile` join at all.
