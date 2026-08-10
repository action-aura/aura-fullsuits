# Phase 9.5B Milestone 8 — Presence Implementation Report

## Real code

`owner/app/employees/presence.py`: `presence_state()` (pure function, no DB access — testable in
isolation), `current_presence_session()` (most-recent non-revoked session across every device),
`employee_presence_state()` (single-employee lookup), `bulk_presence_states()` (one query for an entire
list screen — no N+1, Milestone 5/19's own requirement). Thresholds: `ONLINE_THRESHOLD_SECONDS=120`,
`RECENTLY_ACTIVE_THRESHOLD_SECONDS=900` — named constants, not duplicated magic numbers.

## Real gap found and fixed this milestone

`suspend_employee()`/`terminate_employee()` (Milestone 2) revoke `StaffSession` rows via the existing
`revoke_all_sessions_for_staff()` — but `EmployeePresenceSession` is a **separate** table with its own
`revoked_at`, never touched by that function. Without a fix, a just-suspended employee's dashboard/list
presence badge would keep showing ONLINE/RECENTLY_ACTIVE for up to 15 minutes after suspension (until
their last heartbeat aged out naturally), directly contradicting the spec's own scenario requirement
("Heartbeat is rejected" / suspension takes effect immediately). **Fixed**: a new
`_revoke_all_presence_sessions()` helper in `owner/app/employees/services.py`, called from both
`suspend_employee()` and `terminate_employee()`, in the same transaction as the `StaffSession` revocation.

## Heartbeat endpoint

Real route: `POST /api/operations/v1/presence/heartbeat` — see `employee-operations-api-report.md`
(Milestone 12). Authenticated, session-bound (rejects a heartbeat whose session doesn't match the
caller's own), rate-limited (excess requests within 30s return `200` idempotently, no new row), rejected
immediately after logout/suspension/termination (the session is revoked, `require_login` 401s before the
heartbeat handler ever runs).

## Not audited per-heartbeat (unchanged from Phase 9.5A's own decision)

Confirmed: no `PRESENCE_HEARTBEAT` audit action code was added — `employee-presence-contract.md`'s own
reasoning (volume would dilute the audit log's real security/financial signal) still holds and is not
revisited this phase.

## Real, tested proof

6/6 tests (`owner/tests/test_phase9_5b_presence.py`): no-session → OFFLINE, fresh heartbeat → ONLINE,
exact boundary timestamps (119s/121s/899s/901s) for all three states, revoked session → OFFLINE even if
recent, suspension → immediately OFFLINE (not just after 15 minutes — the real gap above), bulk lookup
matches individual lookups exactly.
