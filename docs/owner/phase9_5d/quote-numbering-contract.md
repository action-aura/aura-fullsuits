# Phase 9.5D — Document Numbering Contract

`app/commercial_sales/numbering.py::allocate_document_number(document_type, *, as_of=None) -> str`

## Format

`{PREFIX}-{YEAR}-{4-digit sequence}`, e.g. `Q-2026-0001`, `SO-2026-0042`, `INV-2026-0007`. Matches the example format already given in `docs/owner/phase9_5a/commercial-document-lifecycle.md`. Prefixes: `QUOTE`→`Q`, `SALES_ORDER`→`SO`, `COMMERCIAL_INVOICE`→`INV`, `COMMERCIAL_REFUND`→`REF`.

## Why genuinely new authority

Milestone 1's audit found no existing sequence generator to reuse — `issue_license_key()`'s format generation and `EmployeeProfile.employee_number` are both "caller supplies a value, DB enforces uniqueness" shapes, not counters. `DocumentNumberCounter` (one row per `(document_type, period_key)`) is new, additive schema (migration `b7e4a2c91f30`).

## Concurrency mechanism (real Postgres semantics, not just documented intent)

1. `SELECT ... FOR UPDATE` on the counter row for `(document_type, str(as_of.year))` — serializes concurrent allocators for the same type+year; a second transaction blocks until the first commits or rolls back.
2. If no counter row exists yet (first allocation of the year for that type), the create-or-find race is handled via a real Postgres `SAVEPOINT` (`db_session.begin_nested()`): the insert attempt is wrapped so that if a concurrent transaction wins the unique-constraint race, only the losing transaction's *nested* savepoint rolls back — never the caller's outer transaction, which may already contain a partially built parent Quote/Order/Invoice row it must not lose.
3. On `IntegrityError` from the losing insert, re-`SELECT ... FOR UPDATE` picks up the winner's row (now visible and lock-free) and proceeds normally.

## Real bugs found and fixed while building this

1. **Wrong assumption in the concurrency-race handling itself, initially**: the very first test run (single-threaded, no race at all) failed with `AttributeError: 'NoneType' object has no attribute 'next_value'` — not a concurrency bug at all. Root cause: the migration created `created_at`/`updated_at` as plain `NOT NULL` with no `server_default`, but `TimestampMixin` (`app/models/base.py`) declares `server_default=func.now()` — a **database-level** default. Every INSERT into the new tables violated the `NOT NULL` constraint, and the resulting `IntegrityError` was (correctly) caught by the code's own race-handling branch — masquerading as a legitimate concurrency conflict. Fixed by adding `server_default=sa.text('now()')` to `created_at`/`updated_at` on all three new tables, matching the exact pattern already used in migration `911ac2a05c12` (Phase 9.5C).
2. **Editing an already-applied migration file doesn't retroactively re-run it**: after fixing the migration source, the real dev DB re-migrated cleanly, but the *test* database (`aura_owner_test`) still had the broken schema from the first `alembic upgrade head` run earlier in this session — Alembic's version table only tracks that revision `b7e4a2c91f30` was applied, not its content. Required an explicit `downgrade -1` + `upgrade head` against `aura_owner_test` specifically to pick up the corrected DDL.

## Concurrency proof (executed, not just claimed)

`tests/test_phase9_5d_numbering.py::test_concurrent_allocation_never_duplicates` — 12 real threads, each with its own Flask app context and thread-local `scoped_session`, all racing to allocate a `QUOTE` number for the same year. Asserts: zero worker exceptions, exactly 12 unique numbers returned, and the sequence is dense (`1..12`, no gaps, no duplicates) — proving the `SELECT ... FOR UPDATE` serialization is real, not just present in the code.

## Test coverage

6 tests: first-of-year format, sequential increment, independent counters per document type, year-boundary reset, unknown-document-type rejection, and the 12-thread concurrency proof above.
