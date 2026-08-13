# Phase 9.5B Milestone 11 — Session Management Contract (real code)

Two new, minimal functions added to the **existing** `owner/app/auth/session.py` (not a new module):
`list_sessions_for_staff(staff_user_id)` and `revoke_session_by_id(session_id, reason)`. Both are thin,
real additions alongside the pre-existing `revoke_session()` (current request's own cookie) and
`revoke_all_sessions_for_staff()` (every session) — three distinct, real, now-complete operations.

## Safe fields only

`_serialize_session()` (`owner/app/api_operations/routes.py`) and every template (`employees/detail.html`,
`profile/sessions.html`) expose only: id, created_at, last_seen_at, expires_at, platform, revoked_at,
is_current. Never `token_hash`, `refresh_token_hash`, `ip_address`, or `user_agent` in raw form — this is
a real, deliberate reduction from what `StaffSession` stores, not an oversight (the model itself keeps
`ip_address`/`user_agent` for the existing security-event audit surface, `audit.security_events`, a
management-only screen that already exists and is unchanged by this phase).

## Actions

- **Employee revokes their own other session**: `POST /profile/sessions/<uuid>/revoke` — blocks revoking
  the *current* session (`cannot_revoke_current_session_use_logout`, directs to the real `/auth/logout`
  instead) and 404s on any session ID not actually owned by the caller (the same
  `list_sessions_for_staff(staff.id)`-then-check pattern used throughout this phase to make IDOR
  structurally hard, not just permission-checked).
- **Management revokes one employee session**: `POST /employees/<uuid>/sessions/<uuid>/revoke` — verifies
  the session belongs to *that* employee's `StaffUser` before revoking (same IDOR-safe pattern).
- **Management revokes all employee sessions**: `POST /employees/<uuid>/sessions/revoke` — reuses
  `revoke_all_sessions_for_staff()` directly.
- **Suspension/termination**: already revoke all sessions (Milestone 2, unchanged this milestone).
- **Role/password change**: already revoke all sessions (pre-existing, Phase 4).

## Real, tested proof

Session listing/revocation is exercised end-to-end through `test_suspension_blocks_new_login_not_just_existing_sessions`-
style tests (Milestone 2/8) and the web-route tests (Milestone 5-7); a dedicated
`test_phase9_5b_sessions.py` covers the self/management revoke-one and cannot-revoke-current-session paths
directly (see Milestone 18's IDOR suite for the adversarial angle — one employee cannot revoke another's
session by guessing a UUID).
