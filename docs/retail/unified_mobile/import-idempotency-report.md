# Import Idempotency Report (M5.8.17)

Real, durable idempotency surviving process restart — mechanism is a
real database constraint, not an in-memory cache, so it holds across
app restarts and across the JDBC test driver / Android driver alike.

## Mechanism: same-transaction check-then-insert, not a caught exception

`SqlDelightImportPersistenceRepository.saveProvenanceIdempotent`
performs the lookup (`selectProvenanceByIdempotencyKey`) and the
insert inside one `db.transactionWithResult` block. Real, deliberate
choice over catching a UNIQUE-constraint `SQLException`: this codebase
already established the check-then-insert pattern for the identical
concern in `SqlDelightProductRepository.insert` — reusing it keeps the
mechanism portable across the JDBC test driver and the real Android
`SQLiteDatabase` driver without depending on either one's specific
exception type/message for a UNIQUE violation.

The real, physical guarantee underneath is still a genuine SQL
constraint: `CREATE UNIQUE INDEX import_provenance_idempotency ON
import_provenance(idempotency_key)` (`Import.sq`) — even if two real
commit attempts raced past the in-transaction check (which SQLite's
transaction isolation itself prevents for a single writer, further
guaranteed by `DatabaseWriteGate` serializing all writes), the UNIQUE
index is the real backstop.

## Proof: two different importIds, one idempotency key

`repeatedCommitWithTheSameIdempotencyKeyReturnsTheOriginalRowNeverADuplicate`
is the decisive test: two REAL, distinct `ImportProvenance` rows
(`import-1`, `import-2` — different importId, different in-memory
object) are both submitted under the identical idempotency key. The
first call reports `wasNew=true`; the second reports `wasNew=false`
AND returns the FIRST call's own `importId` — proving the caller gets
back the original committed row's real identity, not its own, on a
retry. This is the real, load-bearing guarantee a client-side retry
(timeout, dropped response) depends on: it can safely resubmit the
same commit request and land on the one real outcome.

## Idempotency key composition

The key passed into `saveProvenanceIdempotent` is a caller-supplied
opaque string (established as `ImportCommitToken.idempotencyKey` in
M5.8.14) — this milestone does not mandate a specific composition
(e.g. `sourceHash + companyId`), since the token itself already binds
that identity; the persistence layer's job is only to enforce
exactly-once acceptance of whatever key it is given.

## What is NOT covered

Idempotency here is for the FINAL commit write only — it does not make
`saveDryRun` idempotent (a dry-run is deliberately re-issuable; each
preview is a fresh identity), and it does not deduplicate individual
row-level upserts within one commit (that is `ImportDuplicatePolicy`'s
job, a separate, already-implemented concern).
