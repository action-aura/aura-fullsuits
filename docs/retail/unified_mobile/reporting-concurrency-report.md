# Reporting Concurrency Report (M5.6.11)

Closes the M5.5 checkpoint's own explicitly deferred "report read vs sale
commit" concurrency scenario (`stock-concurrency-report.md`'s residual
gap list) now that a real `ReportingRepository` exists.

## Mechanism

`SqlDelightReportingRepository`'s every query method (`getSalesSummary`,
`getSalesTrend`, `getTopProductsByQuantity`, `getTopProductsByNetRevenue`)
acquires the SAME `DatabaseWriteGate.mutex` that every write repository
(`SqlDelightProductRepository`, `SqlDelightInventoryRepository`,
`SqlDelightBranchRepository`, `SqlDelightCategoryRepository`,
`CatalogImporter`) already acquires for its own writes (Part A's real,
executed `DatabaseWriteGate` hardening). A single shared `Mutex` is not
reentrant and serializes ALL acquirers — so a report read and a
multi-statement write (e.g. a future sale finalization inserting one
`sales` row and N `sale_items` rows) can never interleave: the read
either runs entirely before the write's `withLock` block starts, or
entirely after it completes. There is no way for a read to observe the
write's intermediate state.

**Currency resolution is deliberately outside the query's own
`withLock` block** (`SqlDelightReportingRepository`'s own KDoc) —
`SettingsRepository.getSetting` acquires the gate on its own, so nesting
it inside a query's `withLock` would deadlock (`kotlinx.coroutines.sync.Mutex`
is not reentrant). This ordering does not create a consistency gap:
currency is a company-level setting, changing independently of any
single sale/return write, and `resolveCurrency` completing before the
locked query body still means the actual sales/returns rows are read
under the lock, which is the property that matters here.

## Real, executed proof

`ReportConsistencyUnderWritesTest.aReportReadNeverObservesASaleRowWithoutItsCompleteSetOfSaleItems`
(`TEST-com.actionaura.retail.reporting.ReportConsistencyUnderWritesTest.xml`
tests="1" failures="0" errors="0"):

- A writer coroutine simulates 20 future sale finalizations, each
  wrapped in one `gate.mutex.withLock { insertSale(); repeat(5) {
  delay(1); insertSaleItem() } }` — a real `delay(1)` between every item
  insert deliberately maximizes the race window a naive (ungated)
  implementation would expose.
- A concurrent reader coroutine calls
  `getTopProductsByQuantity` 50 times with `delay(1)` between calls,
  recording the observed `net_quantity` each time.
- Assertion: every single observed quantity is a whole multiple of 5
  (one complete sale's item count) — 0, 5, 10, ... 100 are the only
  legal values. Any 1/2/3/4-item partial state would fail the test.
  **Zero partial-state reads were observed across all 50 samples.**

This is a real `kotlinx.coroutines.test.runTest` cooperative-scheduling
race (both coroutines share the test dispatcher and interleave at every
`delay`/`withLock` suspension point), not a simulated/mocked ordering.

## Chart rendering / presentation mapping does not hold the gate

`SqlDelightReportingRepository`'s query methods return plain data
(`SalesSummary`, `SalesTrendResult`, `TopProductsResult`) after their
`withLock` block completes — the lock is released before the caller
receives the result, so nothing above this repository (a future
ViewModel/chart-mapping layer) ever holds the gate while formatting or
rendering.

## Scope of this milestone's proof

No `SaleRepository`/`ReturnRepository` exists yet (M5.5.9/10's own
documented boundary), so this test simulates the future writer directly
rather than exercising a real use case end-to-end. The property proven
is the **mechanism** (shared-gate serialization prevents partial reads),
which will apply automatically to any future writer that follows the
same `gate.mutex.withLock` discipline already established for every
other repository in this codebase — the discipline itself, not this
specific test, is what a future `SaleRepository` implementation must
honor.

## Also closes: backup-vs-write (partial residual note)

`stock-concurrency-report.md` separately deferred "backup vs write
transaction" — still deferred, since no `BackupRepository`
implementation exists yet (M16, `RepositoryBoundaries.kt`). Unaffected
by this milestone.
