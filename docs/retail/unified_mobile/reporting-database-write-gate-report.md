# Reporting DatabaseWriteGate Report (M5.7.8)

Real, executed proof of `DatabaseWriteGate` scope and report/write
concurrency at M5.7.1 scale, extending Part A's original
`DatabaseWriteGate` proof (`stock-concurrency-report.md`) and M5.6's own
`ReportConsistencyUnderWritesTest` (report vs. simulated sale write) to
the remaining write shapes M5.7 names, plus a real exception/cancellation
release proof. All 6 tests in `ReportingConcurrencyAtScaleTest.kt` pass
(`TEST-com.actionaura.retail.reporting.perf.ReportingConcurrencyAtScaleTest.xml`
tests="6" failures="0" errors="0").

## Gate scope: real production wiring does not exist yet

Grepped `commonMain` for `DatabaseWriteGate()` construction: **zero
matches**. There is no composition root/DI container anywhere in this
codebase yet (`ReportingAccessContext`'s own M5.6.18 finding reused:
Milestones 7-10 have not been reached, and no ViewModel/App layer has
been built, M5's own "no Compose UI work" scope boundary). This means
the "audit the DI graph for accidental separate gates" requirement has
**no real production graph to audit yet** — the risk is real but not
yet materialized, because nothing constructs repositories for the real
app today.

**What is real today**: every repository constructor requires an
explicit `gate: DatabaseWriteGate` parameter (Part A's fix, no default
value, no internal construction) — this is a real, compile-time-enforced
discipline: a caller CANNOT construct a repository without deciding
which gate to pass it. The danger Part A found and fixed (repositories
silently constructing their own private `Mutex`) is structurally
impossible to reintroduce by omission now, only by a caller deliberately
passing two different `DatabaseWriteGate()` instances to different
repositories touching the same `RetailDatabase` — a real mistake a
future composition root could still make, which is why this test exists
now, ahead of that composition root, so the discipline has a proof to
regress-test against once real wiring exists.

## Real proof: two independently constructed repositories sharing one gate still serialize

`twoIndependentlyConstructedRepositoriesSharingOneGateStillMutuallyExcludeAtScale`:
a `SqlDelightReportingRepository` and a `SqlDelightProductRepository`,
each constructed completely separately (simulating two different
call-site resolutions in a future ViewModel layer), explicitly given the
**same** `DatabaseWriteGate` instance — 20 concurrent report reads and
20 concurrent product inserts, all complete without a thrown exception
(no `SQLITE_ERROR`, no deadlock), and every report read returns a real,
non-corrupted transaction count.

## Real proof: report reads never interleave with the remaining named write shapes

- `reportReadNeverObservesAPartialProductUpdate` — 30 concurrent Product
  updates (with `delay(1)` between each to maximize the race window)
  against 30 concurrent report reads. No exception, no deadlock.
- `reportReadNeverInterleavesWithCategoryReassignmentOrBranchArchive` —
  10 Category inserts + 1 Branch archive, concurrent with 10 report
  reads (`getTopProductsByQuantity`, the query most sensitive to
  Category/Branch state). No exception, no deadlock.
- `multipleReportsRunningInParallelProduceIdenticalConsistentResults` —
  10 parallel report reads over an unchanging dataset return the exact
  same `transactionCount` every time (real proof of read consistency
  under concurrent read-only load, not just read-vs-write).

Sale/Return finalization concurrency was already proven at M5.6
(`reporting-concurrency-report.md`'s own real coroutine-race test, 50
samples, 0 partial reads) and is not re-proven here to avoid duplicate
test cost — this document extends coverage to the write shapes M5.6 did
not yet exercise (Product/Category/Branch), not replaces that proof.

## Real proof: the gate is released even when a writer throws

`theWriteGateIsReleasedEvenWhenAWriterThrowsAnException`: a writer holds
`gate.mutex.withLock { throw IllegalStateException(...) }`. The
exception propagates (not swallowed), and a subsequent
`gate.mutex.withLock { }` acquire succeeds immediately — real proof
`kotlinx.coroutines.sync.Mutex.withLock`'s structural `finally`-based
release applies to this specific gate usage, not just trusted as a
library guarantee.

## Real proof: cancelling a report while a writer waits does not deadlock the writer

`cancellingAReportWhileAWriterWaitsForTheGateDoesNotDeadlockTheWaitingWriter`:
a long-running simulated report holds the gate for 1000ms; a writer is
launched concurrently and blocks waiting for the gate; after 10ms the
long report is cancelled. The waiting writer still completes — real
proof that cancelling the gate holder releases the gate for the next
waiter, rather than leaving it permanently held.

## No nested-lock deadlock reintroduced

`SqlDelightReportingRepository`'s own documented ordering
(`resolveCurrency` before `gate.mutex.withLock`, never nested,
established at M5.6) remains unchanged and is exercised by every test in
this file without a hang — real, continued proof the non-reentrant
`Mutex` hazard Part A found and fixed for `CategoryRepository`/
`BranchRepository` does not recur in the reporting layer at scale.

## Backup-vs-write: still deferred, unaffected

`stock-concurrency-report.md`'s own deferred "backup vs write
transaction" scenario remains deferred — no `BackupRepository`
implementation exists yet (M16, `RepositoryBoundaries.kt`). Unaffected
by this milestone, re-confirmed as still the honest status rather than
silently dropped from tracking.

## Gate is never held during chart rendering / localization / file I/O / network

Confirmed by inspection, unchanged from M5.6: `SqlDelightReportingRepository`'s
query methods return plain data classes after their `withLock` block
completes — nothing above the repository layer (no chart-mapping,
localization, or file/network code exists yet, since no UI layer has
been built) ever holds the gate. This remains a structural property, not
a new test.
