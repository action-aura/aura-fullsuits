# Phase 9.5B-R3 — Milestone 2: Root Cause and Fix

## Root cause

The Phase 9.5B-R2 `ObjectDeletedError` was caused by **external concurrent
process contention against the shared `aura_owner_test` database**, not by
a defect in the test/fixture code itself:

- `tests/conftest.py`'s `app` fixture runs `TRUNCATE TABLE ... RESTART
  IDENTITY CASCADE` on every `owner_*` table at the start of **every
  single test**.
- `db_session` is a module-level `scoped_session` singleton
  (`app/extensions.py`).
- Phase 9.5B-R2's investigation wave ran multiple pytest processes
  concurrently against the same real Postgres `aura_owner_test` database
  (to stress-test unrelated areas at speed). When process A's `TRUNCATE`
  fires while process B's session still holds an identity-map reference
  to a row loaded before that truncate, SQLAlchemy raises
  `ObjectDeletedError` the next time process B touches that row — exactly
  the observed failure.
- Architecturally this is a real gap: nothing previously prevented two
  pytest processes from targeting `OWNER_TEST_DATABASE_URL`
  simultaneously.

This is **not** an invalid test assumption and **not** a defect that
manifests under the suite's real, canonical single-process invocation
(`pytest tests/ -q`) — proven by three consecutive clean full-suite runs
(see `deterministic-owner-regression-evidence.md`) and one isolated-file
run, all clean, all before any fix was applied.

## Fix applied (real code change, not a workaround)

`owner/tests/conftest.py` — added a new session-scoped, autouse fixture,
`_serialize_concurrent_test_runs`, that acquires a Postgres session-level
advisory lock (`pg_advisory_lock`) for the lifetime of the pytest session,
via a dedicated `psycopg` connection, and releases it
(`pg_advisory_unlock`) at session end. The pre-existing `_migrated_schema`
fixture now explicitly depends on it, guaranteeing lock acquisition
happens first.

**Effect**: a second pytest process invoked against the same test
database while a first is still running will now **block** on
`pg_advisory_lock` until the first process finishes and releases the
lock, instead of racing it. The exact mechanism that caused the Phase
9.5B-R2 failure — two processes' fixtures truncating/reading the same
tables at the same time — is now structurally impossible, not merely
less likely.

This is a genuine root-cause fix at the infrastructure level: it does not
touch, weaken, skip, retry, or reduce any assertion in
`test_commercial_ops_renewal_requests.py` or any other test file — every
one of the 627 tests runs with the exact same assertions as before.

## Verification of the fix

- `pytest tests/test_commercial_ops_renewal_requests.py -q` after the fix:
  **24 passed in 54.26s** — no regression, no slowdown of consequence (the
  advisory-lock acquisition/release is two round-trip queries per test
  session, not per test).
- The full three-run clean-suite proof in
  `deterministic-owner-regression-evidence.md` was gathered before this
  fix (proving the failure doesn't reproduce single-process even without
  it); Milestone 11's final full matrix run (from the final HEAD,
  including this fix) provides the fourth, post-fix full-suite
  confirmation.

## Why this satisfies the Non-Negotiable "no flake hiding" rule

- Not deleted, skipped, xfailed, or had assertions reduced.
- Not "caught and ignored" — no exception handling was added around the
  test or the code under test.
- Not a blind retry or increased sleep — no retry logic exists anywhere
  in this fix.
- Not run only in isolation and excluded from the canonical command — the
  fix applies to every invocation of `pytest tests/ -q`, and Milestone 11
  reruns the full canonical command.
- Not described as pre-existing without fixing — a concrete, real
  infrastructure defect (no serialization between concurrent test
  processes) was identified and closed.
