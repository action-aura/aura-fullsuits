# Phase 9.5B-R3 — Deterministic Regression Plan

## Method (per Milestone 1/2's own required sequence)

1. Reproduce the canonical full-suite failure from a clean process first —
   not the isolated test first.
2. Record: command, Python/pytest versions, env vars, DB config, exact
   failure/stack trace, test order position, preceding tests.
3. Run: failing test alone; failing file alone; failing test after its
   immediate predecessor; suspected contaminating sequence; reversed pair
   order; full suite with alternate collection order where supported.
4. Inspect real contamination sources: SQLAlchemy session/identity-map
   leakage across the `TRUNCATE ... RESTART IDENTITY CASCADE` fixture
   boundary, fixture scope, module-level mutable state, `db_session`
   `scoped_session` registry behavior.
5. Fix the actual root cause (never skip/xfail/retry/exclude).
6. Prove via 3 consecutive clean full-suite runs.

Real, not hypothetical: the observed error
(`sqlalchemy.orm.exc.ObjectDeletedError: Instance StaffUser has been
deleted, or its row is otherwise not present`) is a session-identity-map
symptom, which strongly points at `db_session` (a `scoped_session`)
retaining an identity-mapped `StaffUser` instance across a `TRUNCATE
CASCADE` boundary between two tests — investigated directly in
`flaky-test-reproduction-report.md`.
