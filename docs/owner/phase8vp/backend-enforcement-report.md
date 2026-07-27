# Phase 8V-P — Direct Backend Enforcement Report (Part S)

## Not independently re-exercised this session against a live authenticated product session

Testing this for real requires an authenticated product-level session (Clinic/Retail cashier or
admin login, separate from Owner staff auth) against the real running installs; this session did not
have credentials for the pre-existing local Clinic/Retail installs' own app-level accounts (created
in an earlier, unrelated session) and creating a fresh one was judged lower priority than the
licensing-scenario work above given the time already spent this session.

## What this claim already rests on, real and unchanged

Direct-backend (not UI-only) capability enforcement during restricted commercial states is
`commercial_runtime/licensing_contracts/capability_guard.py` — unchanged by any Phase 8V/8V-P
commit — and is covered by this repository's own existing, real, Postgres/SQLite-backed capability-
guard test suites (`products/clinic/tests/clinic_capability_guard_test.py`,
`products/retail/tests/` equivalents), which assert against the actual backend route handlers
directly, not the frontend. Not re-run as part of this session's regression pass (see
`final-regression-report.md`'s own scoping note on why the full product suites were not re-run this
session — no Python product-backend code changed).

## Result: **NOT INDEPENDENTLY RE-VERIFIED** this session. Relies on pre-existing, unchanged,
already-real test coverage rather than a fresh live proof — disclosed, not claimed as new evidence.
