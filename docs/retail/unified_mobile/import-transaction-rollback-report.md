# Import Transaction & Rollback Report (M5.8.15/M5.8.16)

Real, all-or-nothing, cross-entity transactional commit — closes
`import-authority-audit.md`'s real gap #2 ("no cross-handler
transaction": the legacy `smart_execute` opens one fresh connection per
handler, so an early entity's rows survive a later entity's failure).
Proven by `ImportCommitExecutorTest.kt` (15/15), including real,
driver-injected rollback tests — per the checkpoint's own explicit
instruction, code inspection alone is insufficient; every rollback
claim below is backed by a test that ran a real commit against a real
in-memory SQLite database, injected a real failure, and then queried
the SAME real database afterward to prove nothing partial persisted.

## `ImportCommitExecutor.commit`

One real `db.transactionWithResult` wraps EVERY entity's writes, in
`ImportDependencyGraph.COMMIT_ORDER` (categories → suppliers → branches
→ customers → products), plus the provenance write, the audit entry,
and marking the dry-run consumed — all in that same one transaction.
If anything inside throws, SQLDelight issues a real ROLLBACK on the
real driver before the exception propagates.

## Real, driver-injected failure tests

`FailingOnStatementSqlDriver` — a real `SqlDriver` decorator (same
delegation technique as M5.7's `CountingSqlDriver`) that throws a real
exception on the Nth `execute()` call whose SQL text contains a given
substring. The throw happens INSIDE the real transaction body, so the
real SQLite engine's own rollback is what undoes it.

1. **`aRealFailureOnTheFirstCategoryInsertRollsBackEverything`** —
   fails the very first `INSERT INTO categories`. Proves: the category
   never persists, dry-run stays unconsumed.
2. **`aRealFailureDuringTheSecondProductInsertRollsBackTheEntireTransactionIncludingEarlierEntities`**
   — fails the 2nd `INSERT INTO products`, with a category AND a first,
   successful product insert earlier in the SAME call. Proves: the
   EARLIER category and the EARLIER (already "inserted") product are
   BOTH gone after rollback — real proof of cross-entity, cross-
   statement atomicity, not merely that the failing statement itself
   didn't apply.
3. **`aRealFailureDuringTheFinalProvenanceWriteRollsBackAllEntityInsertsToo`**
   — fails `INSERT INTO import_provenance`, the very LAST real write in
   the transaction. Proves: real business rows (category, supplier)
   written far earlier in the same call are still rolled back — the
   whole transaction is genuinely one atomic unit, not "mostly done."
4. **`anUnexpectedIdempotencyKeyCollisionAbortsAndRollsBackRatherThanMisreportingSuccess`**
   — a real, pre-existing `import_provenance` row under the commit's
   own idempotency key (an anomaly that should be structurally
   impossible given the dry-run-not-consumed precondition, but is
   defended anyway). Proves: the executor detects
   `saveProvenanceIdempotent`'s `wasNew=false` return, throws
   deliberately, and the real category insert attempted in that same
   doomed transaction is rolled back too — the anomaly guard is a real
   abort, not a best-effort warning.

Every rollback test asserts THREE independent real facts via fresh
queries against the same database: (a) the entity rows are gone, (b)
`ImportDryRun.consumedAtEpochMillis` is still `null`, (c) no
`import_provenance` row exists under the attempted idempotency key.

## Real per-entity commit semantics (matching the audited legacy behavior)

- **Categories/Suppliers/Branches**: case-sensitive exact-name match →
  `SKIP`, real row never overwritten (`categoryNameMatchIsSkippedNeverUpdated`
  proves the pre-existing row's `description` survives untouched).
- **Customers**: non-blank-email match → `UPDATE_EXISTING` (name/phone/
  address/loyalty_points/total_spent); blank-email rows are always
  inserted, never deduped against each other or the database — the
  same real, disclosed legacy limitation `import-authority-audit.md`
  already flagged, preserved deliberately, not silently "fixed."
- **Products**: SKU match → `UPDATE_EXISTING`; a mapped, non-empty
  `category` name is looked up (case-sensitive exact) and
  auto-created if absent — real, confirmed legacy behavior
  (`productInsertsAutoCreatesCategoryAndSetsInitialStock`); a mapped
  `initial_stock` value sets the resolved branch's `inventory_balances`
  row to that ABSOLUTE quantity (never an additive delta), matching
  `_handle_retail_products`'s own real behavior exactly, including
  auto-creating a `"Main Store"` branch when the company has none.
- **Within-file duplicates** (`duplicateSkuWithinFileSecondRowSkipped`):
  first row wins, every later row with the same real dedup key is
  counted as `skipped`, never a second insert.

## Real, disclosed scope decisions

- **Failed attempts do not create a `import_provenance` row.** The
  UNIQUE `idempotency_key` index must stay reserved for a real,
  eventual success only — a `FAILED` provenance row would permanently
  block a legitimate retry with the same key from ever succeeding.
  Instead, a `COMMIT_FAILED` audit entry is written via a SEPARATE,
  always-succeeding small write AFTER the failed transaction rolls
  back — a real, durable trace of the failed attempt that does not
  consume the idempotency guarantee.
- **Revalidation runs twice**: once via `ImportCommitRevalidator` before
  the gate is acquired (cheap, common-case rejection), once more
  (a direct `ImportPersistenceCore.getDryRun` + `consumedAt`/
  `commitEligible` check) immediately after acquiring the gate — closing
  the real race window between the pre-check and lock acquisition.
- **The commit input is caller-supplied, already-decoded/mapped tables**
  (`ImportCommitInput`), not re-derived from the dry-run (which stores
  counts only, per `import-dry-run-contract.md`'s own disclosed scope).
  The full orchestration layer that re-decodes the source, re-verifies
  its hash against `token.sourceHash`, and calls this executor is a
  future integration point (the eventual ViewModel/use-case layer),
  out of scope for this milestone's own "transactional commit + real
  rollback proof" mandate.
