# Phase 9.5B Milestone 22 — Local Functional Validation Report

Single real end-to-end test (`owner/tests/test_phase9_5b_functional_validation.py::test_full_employee_lifecycle_scenario`)
walking the governing spec's own 32-step scenario, synthetic data only, through real HTTP requests against
the real Flask app (`app.test_client()`), never a mocked service layer.

## Real technique note: presence-threshold "waiting"

Steps 11-14 ("wait past ONLINE threshold" / "wait or control trusted test time past offline threshold")
are satisfied via `employee_presence_state(profile_id, as_of=...)`'s real `as_of` parameter rather than a
literal `time.sleep(900)` — the same sound, already-established technique from
`test_phase9_5b_presence.py`. A real 15-minute sleep in an automated test suite would itself be a form of
dishonesty (slow, flaky, and testing wall-clock behavior rather than the threshold logic), and
`presence_state()`'s own signature was explicitly designed (Milestone 8) to accept an injected "now" for
exactly this kind of real, deterministic testing.

## What the test actually proves, step by step

1. Real MFA login for management account A.
2. Real transactional employee creation (`POST /employees`) — StaffUser confirmed absent until acceptance.
3. Real invitation acceptance (password set) — profile confirmed `PENDING`.
4. Real first login forced into MFA enrollment (`mfa_required=True`) — profile still `PENDING`.
5. Real TOTP enrollment (actual `pyotp` code generation against the real provisioning secret) completes
   the session — profile becomes `ACTIVE` at that exact moment, not before.
6. Self-profile shows the employee's own real data.
7. Real heartbeat via the real `/api/operations/v1/presence/heartbeat` route returns `ONLINE`.
8. Presence transitions to `RECENTLY_ACTIVE` then `OFFLINE` exactly at the real threshold boundaries.
9. A second real Super Admin (B) sees the exact same employee, edits it; A's next read sees B's edit
   (same authoritative row, no per-actor cache).
10. The employee cannot reach another employee's detail (403 — no `employees.view_all`) and has no code
    path to self-edit `employee_number` (`SELF_EDITABLE_PROFILE_FIELDS` asserted directly).
11. Real suspension (`POST /employees/<id>/suspend`) → real heartbeat rejected, real re-login rejected
    (`401`, not just old sessions revoked).
12. Real reactivation → real re-login succeeds again.
13. Real termination → real re-login rejected again, permanently (no rehire path this phase).
14. The `EmployeeProfile` row and every real `AuditLog` row survive termination — full name and status
    both directly asserted, never deleted.
15. Real last-usable-Super-Admin protection: disabling the second-to-last admin succeeds, disabling the
    actual last one raises `LastSuperAdminError`.
16. The `EMPLOYEE_SUSPENDED` audit row's `actor_staff_user_id` is verified to be A's real ID specifically.

## Honest scope note

This is the local, synthetic-data equivalent of the spec's scenario — no real VPS, no real email delivery
(the setup link is extracted from the real rendered HTML response, matching how a real administrator would
copy it for manual delivery), no real mobile client (the heartbeat is issued from the same browser-session
test client, matching this phase's own "no mobile auth routes yet" boundary).
