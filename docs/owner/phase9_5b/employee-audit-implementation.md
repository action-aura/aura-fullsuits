# Phase 9.5B Milestone 16 — Employee Audit Implementation

Extends `docs/owner/phase9_5a/audit-event-catalog.md` (same free-text `action_code` authority,
`app.audit.services.record()` — no fixed enum, no second audit table). Real, grep-verified list of every
`action_code` this phase's code actually emits (`owner/app/employees/`, `owner/app/staff/`,
`owner/app/api_operations/`):

| action_code | Emitted by | Real (was reserved in 9.5A)? |
|---|---|---|
| `EMPLOYEE_PROFILE_CREATED` | `create_employee_profile()` | Already real (9.5A) |
| `EMPLOYEE_PROFILE_UPDATED` | `update_employee_profile()` | Already real (9.5A) |
| `EMPLOYEE_ACTIVATED` | `activate_employee()` | **New this phase** |
| `EMPLOYEE_SUSPENDED` | `suspend_employee()` | Already real (9.5A) |
| `EMPLOYEE_REACTIVATED` | `reactivate_employee()` | **New this phase** |
| `EMPLOYEE_TERMINATED` | `terminate_employee()` | Already real (9.5A) |
| `EMPLOYEE_ARCHIVED` | `archive_employee()` | **New this phase** |
| `EMPLOYEE_SELF_PROFILE_UPDATED` | `update_own_profile()` | **New this phase** |
| `EMPLOYEE_SESSIONS_REVOKED` | management "revoke all sessions" route (both web + API) | **New this phase** |
| `EMPLOYEE_SESSION_REVOKED` | management "revoke one session" route | **New this phase** |
| `STAFF_INVITATION_CREATED` | `create_invitation()` (called by `create_employee_invitation()`) | Reused unchanged (Phase 5) |
| `STAFF_INVITATION_REVOKED` | `revoke_invitation()` (called by `reissue_employee_invitation()`) | Reused unchanged |
| `STAFF_ROLES_CHANGED` | `assign_roles()` (reused unchanged by the employee-detail "change role" form) | Reused unchanged |
| `STAFF_DISABLED` | `disable_staff()` (now also last-SUPER_ADMIN guarded) | Reused, hardened |
| `STAFF_MFA_RESET` | `staff.reset_mfa` (unchanged, linkable from employee detail in a future phase) | Reused unchanged |
| `MFA_ENROLLED`, `STAFF_LOGIN_SUCCESS`, `STAFF_LOGIN_MFA_SUCCESS`, `STAFF_INVITATION_ACCEPTED` | pre-existing auth flow, unchanged | Reused unchanged |

## Deliberately NOT audited: presence heartbeats

Confirmed unchanged from Phase 9.5A's own decision (`employee-presence-contract.md`): `touch_presence()`
never calls `audit_record()`. A once-per-30-second-at-most operational signal would dilute the audit log's
real security/financial signal value — recorded here again explicitly since this phase is where a
heartbeat *route* (`/api/operations/v1/presence/heartbeat`) first actually exists to be tempted to audit.

## Self-attribution proof (two-management-account requirement)

Every `action_code` above carries `actor_staff_user_id` from the real authenticated caller — never a
shared/generic "admin" identity. `management-synchronization-evidence.md` (Milestone 17) proves this
concretely with two separate synthetic Super Admin accounts.

## Safe payloads (unchanged discipline)

`audit.services.redact()` already strips any field whose name matches `password`/`token`/`secret`/etc. —
confirmed no new audit call in this phase passes a raw session token, setup-token, or password anywhere
in `before_state`/`after_state` (every call above passes only status enums, employee numbers, and
session/entity UUIDs).
