# Phase 9.5B-R3 — Milestone 1: Flaky Test Reproduction Report

## Identity of the failing test (from Phase 9.5B-R2)

`tests/test_commercial_ops_renewal_requests.py` — an `ObjectDeletedError`
raised from SQLAlchemy's identity map during a renewal-transition test,
observed once during Phase 9.5B-R2's heavy multi-process test-running
wave.

## Environment (real, recorded)

- Python 3.11.9
- pytest 8.3.2 (pinned in `requirements/development.txt`; upgrade to
  9.0.3 deliberately deferred — see `dependency-scan-final.md`)
- DB: PostgreSQL, `OWNER_TEST_DATABASE_URL` default
  `postgresql+psycopg://aura_owner:aura_owner_dev@localhost:5432/aura_owner_test`
  (from `tests/conftest.py:11-12`)
- Fixture architecture: session-scoped `_migrated_schema` (runs Alembic
  once), function-scoped `app` fixture that `TRUNCATE`s all `owner_*`
  tables `RESTART IDENTITY CASCADE` at setup and calls `db_session.
  remove()` at teardown (`tests/conftest.py`).
- `db_session` is a module-level `scoped_session` singleton
  (`app/extensions.py`), shared across the whole pytest process.

## Reproduction attempts, in the order the governing spec required
   (full suite first, not the isolated file first)

| # | Command | Result |
|---|---|---|
| 1 | `pytest tests/ -q` (from `owner/`), clean process, no concurrent DB access | 627 passed, 0 failed, 1188.55s |
| 2 | `pytest tests/ -q`, clean process, no concurrent DB access | 627 passed, 0 failed, 1201.21s |
| 3 | `pytest tests/ -q`, clean process, no concurrent DB access | 627 passed, 0 failed, 1233.23s |
| 4 | `pytest tests/test_commercial_ops_renewal_requests.py -q` (isolated file) | 24 passed, 0 failed |

Three consecutive full-suite runs and one isolated-file run all passed
cleanly. The originally observed failure did **not** reproduce under
controlled, single-process conditions at any point in this wave.

## Working hypothesis (carried into Milestone 2)

The Phase 9.5B-R2 failure occurred during a wave of heavy, deliberately
concurrent multi-process test execution against the same
`aura_owner_test` database (multiple pytest invocations targeting
overlapping tables at once, run to stress-test other areas). An
`ObjectDeletedError` is SQLAlchemy's signal that a row backing a loaded
ORM object was deleted out from under an open session/identity-map entry
— exactly the failure mode expected when a second, concurrent process's
`TRUNCATE ... RESTART IDENTITY CASCADE` (from another test process's `app`
fixture setup) fires while a first process's test still holds a session
referencing rows from before the truncate. This is external
process-contention, not a defect in this suite's own fixture design run
single-process.

See `flaky-test-root-cause-and-fix.md` for the full root-cause
determination and the fix action taken.
