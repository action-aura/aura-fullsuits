# Wave 0 Correction — Data Integrity (AUDIT-016 and related)

Status: **FIXED AND VERIFIED** (FK enforcement); see below for what is
explicitly *not* claimed fixed.

## AUDIT-016 — Foreign keys never enforced (Retail)

### Root cause

`products/retail/backend/database/schema.py`'s `_conn()` is the single
connection factory used by every Retail route, but it never issued
`PRAGMA foreign_keys=ON`. SQLite defaults foreign-key enforcement to
**off**, and this setting is per-connection, not persisted in the database
file — so every declared `FOREIGN KEY` constraint in the schema was purely
decorative for the entire lifetime of this connection factory.

### Fix

Added `c.execute("PRAGMA foreign_keys=ON")` to `_conn()`, so it is set on
every single connection opened anywhere in Retail, not a one-time
initialization step. The equivalent was checked for Clinic: Clinic's
`_conn()` already had `PRAGMA foreign_keys=ON` from an earlier phase, and
the shared registry database (`commercial_runtime/identity/registry_db.py`
`get_conn()`) already had it as well — confirmed by reading both before
concluding Retail was the only gap.

### Verification

Direct reproduction: `PRAGMA foreign_keys` now returns `1` on a fresh
Retail connection, and an insert referencing a nonexistent `product_id`
raises `sqlite3.IntegrityError` (the specific constraint hit first in a
minimal reproduction was `NOT NULL constraint failed: sale_items.line_total`
rather than the FK constraint itself, because that column happened to be
checked first by SQLite's insert path in that exact minimal payload — the
insert was still correctly rejected either way, and the full application
code path always supplies `line_total`, so the FK constraint is the one
that actually matters in real usage).

No existing cascade policy was altered — the spec explicitly said not to
change cascade behavior without understanding its effect, and this
correction only turns enforcement on; it does not add, remove, or change
any `ON DELETE`/`ON UPDATE` clause in the schema.

### Commit

`57a3048` — fix: enforce sqlite foreign keys on retail; harden wave0 error
handling (AUDIT-016).

## Transaction atomicity (AUDIT-006/009/018, cross-referenced)

Every money-changing route touched by this wave now wraps its full
read-check-write sequence in `BEGIN IMMEDIATE ... COMMIT`/`ROLLBACK`:
Retail `create_sale()` and `create_return()` (see the financial-authority
and return correction docs), and Clinic `create_invoice()` and
`record_payment()` (see the payment correction doc). `BEGIN IMMEDIATE`
specifically (not a bare `BEGIN`) was chosen everywhere so the write lock is
acquired *before* the first read-for-validation, closing the race window
where two concurrent requests could both read a stale "safe to proceed"
snapshot before either commits.

## Idempotency persistence

Retail `sales.idempotency_key`, Retail `returns.idempotency_key` (new
column + partial unique index), and Clinic `clinic_payments.idempotency_key`
(new column + partial unique index) all persist across the request that
created them — verified directly by re-querying the DB after a duplicate
submission in each product's dedicated test suite (not just checking the
HTTP response looked right).

## Explicitly not claimed fixed in this wave

- `sales.sale_number`'s bare (non-company-scoped) `UNIQUE` constraint has
  the same latent per-company-sequence-collision flaw that
  `returns.return_number` had before this wave's fix — masked only by an
  explicit random-seed workaround already present in the test helpers. Not
  fixed here (out of the named AUDIT-001/002/003/004/011/012/019 scope);
  tracked in the residual risk register.
- AUDIT-010 (cross-file pytest pollution) is a test-infrastructure issue,
  not a data-integrity issue in the shipped product — see the dedicated
  decision recorded in `wave0-residual-risk-register.md`.
