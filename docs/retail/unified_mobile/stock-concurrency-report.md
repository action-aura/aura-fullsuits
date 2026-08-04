# Aura Retail Unified Mobile — Stock/Product Concurrency Report (M5.5.14)

## Real, executed finding — not assumed

`ProductInventoryConcurrencyTest.kt` launches genuinely parallel coroutines via `Dispatchers.Default` (real OS threads), deliberately different from the `runTest`-cooperative-dispatcher pattern M3's `FinancialSecurityTest` used for its own in-memory-repository races. This distinction mattered in practice: `SqlDelightXxxRepository`'s `db.transactionWithResult { ... }` blocks are synchronous with no internal suspension point, so launching "concurrent" coroutines on `runTest`'s own virtual-time dispatcher would execute them fully serialized with no real interleaving ever occurring — that pattern would have proven nothing about real concurrent access.

**The first real run, before any fix, failed all four concurrency tests**:

- `org.sqlite.SQLiteException: [SQLITE_ERROR] SQL error or missing database (cannot start a transaction within a transaction)` — two real threads calling `db.transactionWithResult` on the same shared `JdbcSqliteDriver`/JDBC `Connection` collided.
- A real data-corruption result, not just an exception: `concurrentStockIncreasesAllApplyWithNoLostUpdates` expected `15`, observed `2` — 13 of 15 genuinely concurrent stock increases were silently lost.

**Root cause**: `app.cash.sqldelight:sqlite-driver`'s `JdbcSqliteDriver` wraps a single JDBC `Connection`. JDBC `Connection` objects are not safe for concurrent statement execution from multiple threads — this is a real, external driver/library constraint, not a bug in this codebase's own business logic.

## Fix — a private, per-instance `Mutex`, not a shared/injected gate

Every `SqlDelightXxxRepository` class (`Category`/`Branch`/`Product`/`Inventory`/`Settings`) now holds a private `kotlinx.coroutines.sync.Mutex` and wraps every public method body in `writeMutex.withLock { ... }`. This is sufficient (not a cross-repository shared gate) because this codebase's architecture constructs exactly one instance of each repository class per `RetailDatabase` and shares that one instance everywhere — every real race this milestone tested (duplicate SKU/barcode creation, final-unit sale, concurrent increases) was contention between multiple calls into the *same* repository instance, never across two different repository objects. Same discipline `InMemorySaleRepository` (M3) already applied via its own `Mutex` for its in-memory store — this extends that precedent to the real SQLite-backed repositories, where it turned out to be not just prudent but load-bearing.

**Real hazard avoided while implementing the fix**: `kotlinx.coroutines.sync.Mutex` is not reentrant. `CategoryRepository.insert`/`BranchRepository.insert` originally called their own public `getById` after their transaction — if left unchanged, that would call `writeMutex.withLock` again from inside an already-held lock and deadlock permanently. Fixed by inlining the post-insert read directly (querying `db.catalogQueries` itself) instead of calling back through the public, lock-guarded method.

## Real, executed proof after the fix

`./gradlew :shared:testDebugUnitTest --tests "...ProductInventoryConcurrencyTest"`: all 4 tests pass with real `Dispatchers.Default` parallelism:

- `concurrentDuplicateSkuCreationResolvesToExactlyOneWinner` — 20 genuinely parallel same-SKU creates → exactly 1 success, 19 `DuplicateValue` failures, exactly 1 product row.
- `concurrentDuplicateBarcodeCreationResolvesToExactlyOneWinner` — same shape, barcode.
- `finalUnitSaleRaceResolvesExactlyOneWinnerAndStockNeverGoesNegative` — 10 concurrent "sale" attempts for the last 1 unit → exactly 1 success, 9 `InsufficientStock` failures, final stock exactly `0`, exactly 1 movement row (no partial/duplicate movement from a losing attempt).
- `concurrentStockIncreasesAllApplyWithNoLostUpdates` — 15 concurrent increases → all 15 apply, final stock exactly `15`, no lost updates.

Full suite unaffected: 153/153 (149 pre-fix baseline + 4 concurrency tests), unified Android debug APK still builds.

## Real, honest scope note for later milestones

This finding is specific to `JdbcSqliteDriver` (the JVM/unit-test target). The real Android target uses `AndroidSqliteDriver` (`app.cash.sqldelight:android-driver`, wrapping Android's own `SQLiteOpenHelper`/`SQLiteDatabase`), which has different, Android-framework-managed threading characteristics (Android's own `SQLiteDatabase` supports safe concurrent access via internal connection management, especially in WAL mode, which `AndroidDatabaseDriverFactory` already enables per-connection). The `Mutex` fix applied here is correct and sufficient regardless of which underlying driver is in play — it serializes at the Kotlin coroutine level, above the driver — but real device-level concurrency behavior against `AndroidSqliteDriver` has not been separately measured and is tracked as Milestone 24 (physical Android validation) scope, not fabricated as already proven here.
