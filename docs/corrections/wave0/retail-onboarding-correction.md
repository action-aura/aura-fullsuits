# Wave 0 Correction — Retail Onboarding (AUDIT-001)

Status: **FIXED AND VERIFIED**

## Root cause

`products/retail/backend/app.py` never imported or registered
`commercial_runtime.identity.onboarding_routes.onboarding_bp` — Clinic's
`app.py` did, Retail's did not. Every onboarding route (including
`POST /api/onboarding/create-admin`) 404'd on Retail. This was traced fully
before concluding the fix was legitimate: `create_admin()` itself, and every
table/function it depends on (`registry_db.users`, `registry_db.company_settings`,
`hash_password`, `create_session`), was already present and already used by
the rest of Retail's auth stack — only the blueprint registration was
missing. This was not "assume it's a two-line fix without checking"; the
full onboarding flow was read end-to-end first (see the pre-fix reproduction
report's AUDIT-001 section).

## What was fixed

1. **The missing route registration** (`products/retail/backend/app.py`):
   added the same `onboarding_bp` import/registration Clinic already had.
2. **Two real bugs found while tracing `create_admin()` itself**, both
   pre-existing and not previously covered by any test:
   - A second onboarding attempt reusing an email already registered to an
     *unrelated* account used to **silently delete that account**
     (`DELETE FROM users WHERE email=?`) before inserting the new admin.
     Now: the email collision is rejected (400) unless the row being
     replaced is the *same* pending-admin row being finalized.
   - The `company_settings` insert lived outside the `users` insert's
     transaction, wrapped in its own swallowed `try/except: pass` — a
     mid-onboarding failure could leave an admin account with no matching
     company settings. Now both inserts commit atomically inside one
     `BEGIN IMMEDIATE` transaction.
   - `BEGIN IMMEDIATE` was added so two concurrent onboarding requests
     cannot both observe "no admin exists yet" and both proceed.

## Full clean-install flow validated

1. `GET /api/onboarding/status` on an empty DB → `needs_setup: true`.
2. `POST /api/onboarding/create-admin` with valid data → `200`, admin row +
   `company_settings` row created atomically, session established.
3. `GET /api/onboarding/status` again → `needs_setup: false`.
4. `POST /api/auth/login` with the new admin's credentials → `200`.
5. A second `POST /api/onboarding/create-admin` once a real admin exists →
   `409`, no new row, existing admin untouched.
6. Weak password (< 6 chars) → `400`, no row created.
7. Reusing an unrelated account's email once an admin exists → `400`/`409`,
   that account survives untouched (regression test for the delete bug).
8. A fresh Flask app instance booted against the same on-disk
   `AURA_APP_DATA` (restart-equivalent) still reports `needs_setup: false`
   and the original admin can still log in — the database, not any
   in-memory or config.json state, is authoritative.
9. Calling `create-admin` directly again after setup (bypassing any UI) is
   still rejected the same way — the fix enforces the actual business rule,
   not just the route's existence.

## Tests

`products/retail/tests/retail_onboarding_wave0_test.py` (8 tests, all
passing): clean-install status, admin creation, login, duplicate rejection,
weak-password rejection, email-collision-preserves-unrelated-account,
restart-equivalent persistence, direct-API-abuse-after-setup rejection.

## Commit

`7d05a93` — fix: restore retail first-run onboarding (AUDIT-001).

## Residual risk

None known for the onboarding flow itself. Employee invite/setup
(`employee_setup`) and admin-employee-management routes were read but not
modified in this wave — they were not part of AUDIT-001's scope and no
audit finding named them as stop-ship.
