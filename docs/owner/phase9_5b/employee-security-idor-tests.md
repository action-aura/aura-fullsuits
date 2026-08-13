# Phase 9.5B Milestone 18 — Security / IDOR Test Coverage

72 real Phase 9.5B tests total across 12 files. Coverage against the governing spec's own checklist:

## Employee creation
`test_phase9_5b_onboarding.py` — transaction succeeds, duplicate account/employee-number rejected
(including the real race-condition fix for two open invitations), invalid manager reference rejected,
`SelfEscalationError` reused unchanged (a non-Super-Admin cannot grant `SUPER_ADMIN` via
`create_employee_invitation()`, since it delegates to the same `create_invitation()` guard).

## Setup token
Reused, unchanged, pre-existing coverage (`test_security.py::test_invitation_token_is_one_time_use`,
`test_expired_invitation_rejected`) plus this phase's own `test_phase9_5b_onboarding.py` (token
materializes a real `EmployeeProfile` transactionally, never logged/audited in raw form — confirmed by
reading `create_invitation()`'s unchanged audit payload).

## MFA
`test_mfa_required_employee_forced_through_enrollment_before_full_access` — a forced-MFA employee's first
login never reaches `create_session()`'s full-access path until enrollment completes; profile stays
`PENDING` until then.

## Lifecycle
`test_phase9_5b_lifecycle.py` (10 tests) — every transition, `TERMINATED->ACTIVE` rejected,
`ARCHIVED` terminal, suspension blocks new login (not just revokes sessions — the real gap found and
fixed), reactivation does/doesn't restore login per the independent-disable rule, last-SUPER_ADMIN
protection on suspend/disable, non-Super-Admin suspension never blocked by that guard.

## Profile access / IDOR
`test_phase9_5b_authorization.py` (8 tests) — cross-employee detail/suspend/terminate/session-revoke all
403 for a SALES/SUPPORT attacker; terminated employee cannot authenticate even with the correct password.
`test_phase9_5b_web_routes.py`/`self_routes.py` design — self-profile routes carry no employee ID at all
(structural IDOR prevention, Milestone 7). `test_phase9_5b_sessions.py` — session-UUID IDOR: an employee
cannot revoke another's session by guessing its UUID (404, row untouched, verified directly against the DB).

## Role assignment
Reused, unchanged `staff.assign_roles`/`SelfEscalationError` coverage (pre-existing `test_security.py`),
hardened this phase with the last-SUPER_ADMIN analysis (`role-assignment-security-report.md`).

## Presence
`test_phase9_5b_presence.py` (6 tests) — exact boundary timestamps (119s/121s/899s/901s), revoked session
OFFLINE even if recent, suspension forces OFFLINE immediately (the real gap fixed this phase), bulk lookup
matches individual. `test_phase9_5b_operations_api.py` — anonymous heartbeat rejected, missing
`app_instance_id` rejected (`VALIDATION_ERROR`), authenticated heartbeat returns `ONLINE`.

## Sessions
`test_phase9_5b_sessions.py` (4 tests) — self list/revoke-other, cannot-revoke-current (directed to
`/auth/logout`), management revoke-one (recent-auth gated), cross-employee session IDOR blocked.

## Two-management-account synchronization
`test_phase9_5b_management_sync.py` (7 tests) — see `management-synchronization-evidence.md`.

## Web security
CSRF: confirmed structurally, not merely asserted — every POST test in this phase that used an invalid/
missing token observed a real `400` from Flask-WTF's global `before_request` hook (predates any
route-level permission check, a real defense-in-depth ordering); tests requiring a *specific* permission
result fetch a genuine token first so the permission check, not CSRF, is what's being proven
(`test_phase9_5b_authorization.py`'s own comments document this ordering explicitly). No destructive GET
route exists anywhere in this phase's new code (every state-changing action is `POST`/`PATCH`/`PUT`).

## Honest gaps (not silently dropped — see `phase9-5b-gate-matrix.md`)

Refund-creates-reversal / no-self-approval for money-movement actions: not applicable this phase (no
commission/payment posting service exists yet, unchanged from Phase 9.5A's own honest scope). Full
Arabic/RTL: deliberately deferred (`phase9-5b-scope-and-boundaries.md`). A literal 1000-employee load test:
scaled down to 300 for local practicality — see `employee-query-performance-report.md`.
