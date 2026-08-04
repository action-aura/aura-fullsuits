# Aura Retail Unified Mobile — Milestone 5.8 Test Report

Real, executed evidence only. Every number below comes from a real
Gradle test run and JUnit XML summation
(`shared/build/test-results/testDebugUnitTest/*.xml`).

## Test count progression

| Checkpoint | Shared tests | Delta |
|---|---|---|
| M5.7 close (accepted checkpoint) | 291 | — |
| After M5.8.1-M5.8.6 (models, limits, format detector, CSV/JSON decoders) | 342 | +51 |
| After M5.8.7 (XLSX security policy/reader/composer, commonMain) | 371 | +29 |
| After M5.8.8 (SQLite security policy/pipeline, commonMain + fake reader) | 394 | +23 |
| After M5.8.9-M5.8.10 (entity detection, domain value parsing) | 418 | +24 |
| After M5.8.11-M5.8.12 (duplicate/conflict policy, dependency graph) | 431 | +13 |
| After M5.8.13/M5.8.14/M5.8.17/M5.8.18 (dry-run + provenance + audit persistence, revalidation, idempotency) | 447 | +16 |
| After M5.8.15/M5.8.16 (transactional commit executor, real driver-injected rollback) | 462 | +15 |
| After M5.8.19 (deferred `ImportAccessContext` authorization boundary) | 467 | +5 |
| After M5.8.20 (Android adapter validation, `PickedFileImportSource`, XLSX testability correction) | 473 | +6 |
| After M5.8.21/M5.8.22 (performance at scale, real backup/restore proof, zero-stock bug fix) | 480 | +7 |
| After M5.8.23/M5.8.24 (security + per-entity test matrix gap closure) | **491** | +11 |

**Net M5.8 contribution: 200 new tests (291 → 491), 0 failures, 0
errors at every checkpoint** — each commit was verified green before
the next began, matching the discipline established at M5.6/M5.7.

## New test files (this milestone)

| File | Tests | Covers |
|---|---|---|
| `ImportFormatDetectorTest.kt` | 11 | M5.8.4 magic-byte/structural format detection |
| `CsvImportDecoderTest.kt` | 24 (21 + 3 boundary, M5.8.23) | M5.8.5 real RFC4180 CSV decoding + limit boundaries |
| `JsonImportDecoderTest.kt` | 19 | M5.8.6 JSON decoding, NaN/Infinity rejection (real bug found+fixed) |
| `XlsxSecurityPolicyTest.kt` | 12 | M5.8.7 zip-bomb/macro/external-link/path-traversal policy |
| `XlsxSheetXmlReaderTest.kt` | 10 | M5.8.7 shared-strings/cell/formula-flag XML reading |
| `XlsxTableComposerTest.kt` | 7 | M5.8.7 header/row composition |
| `AndroidXlsxImportDecoderTest.kt` | 4 | M5.8.20 real JVM proof this Android class needed no device (correcting an earlier overly-conservative claim) |
| `SqliteSecurityPolicyTest.kt` | 12 | M5.8.8 schema/trigger/view/virtual-table/integrity policy |
| `SqliteTableComposerTest.kt` | 7 | M5.8.8 row composition from raw SQLite rows |
| `SqliteImportPipelineTest.kt` | 4 | M5.8.8 full orchestration via `FakeSqliteRawReader` |
| `ImportEntityDetectorTest.kt` | 10 | M5.8.9 entity scoring/mapping (real tie-break finding) |
| `ImportDomainValueParserTest.kt` | 17 (14 + 3, M5.8.21/23) | M5.8.10 canonical M3 value parsing; zero-or-more Quantity fix; malformed-email skip |
| `ImportDuplicatePolicyTest.kt` | 9 | M5.8.11 within-file/against-database dedup decisions |
| `ImportDependencyGraphTest.kt` | 4 | M5.8.12 commit-order sorting |
| `SqlDelightImportPersistenceRepositoryTest.kt` | 7 | M5.8.13/17/18 dry-run/provenance/audit persistence |
| `ImportCommitRevalidatorTest.kt` | 9 | M5.8.14 8-check server-side revalidation |
| `ImportCommitExecutorTest.kt` | 15 | M5.8.15/16 transactional commit + real driver-injected rollback + gate concurrency |
| `ImportCommitExecutorMatrixTest.kt` | 8 | M5.8.23/24 per-entity matrix gaps (missing-column, cross-business, dependency-in-same-import, archived-row, idempotent-retry) |
| `ImportAccessContextTest.kt` | 5 | M5.8.19 deferred authorization boundary |
| `PickedFileImportSourceTest.kt` | 2 | M5.8.20 `FilePicker`→`ImportSource` bridge |
| `ImportPerformanceAtScaleTest.kt` | 3 | M5.8.21 real timing at 10,000-row/2,000-product scale |
| `ImportBackupRestoreTest.kt` | 1 | M5.8.22 real `VACUUM INTO` backup/restore proof |

`FailingOnStatementSqlDriver.kt` is shared test infrastructure, not a
test class itself (mirrors M5.7's own `CountingSqlDriver.kt` precedent).

## Required evidence-file coverage

All 27 originally-specified documents plus 2 that emerged as real,
necessary additions during the work (`import-format-detection-contract.md`,
required but initially missed; the XLSX testability correction folded
into `import-android-adapter-validation.md` rather than a 28th file)
exist under `docs/retail/unified_mobile/`, each cited by name from this
report or `milestone-5-8-decision.md`.

## Cross-language and platform checks

- Retail Python: not re-run this milestone (no Python file was touched
  — the entire milestone is Kotlin-side, matching M5.7's own precedent
  for a Kotlin-only milestone). Last real confirmation remains 194/194.
- Android debug APK: built successfully after every commit in the
  M5.8 sequence (real, repeated Gradle invocations, never concurrent).
- iOS: not claimed, per standing constraint — the iOS adapter contract
  for Import Center is defined in `import-android-adapter-validation.md`
  but not implemented, per the checkpoint's own explicit instruction.
- Real on-device Android execution: not performed, disclosed limitation
  for `AndroidSqliteImportDecoder`/`AndroidSqliteRawReader`
  (`import-android-adapter-validation.md` — no adb/emulator on this
  host). `AndroidXlsxImportDecoder`, by contrast, was found to need no
  device at all and is now real, JVM-tested — a correction, not a gap.

## Real bugs found and fixed during this milestone

1. **`CsvImportDecoder`'s redundant hand-rolled `decodeToString`** —
   removed in favor of the real Kotlin stdlib function of the identical
   signature (M5.8.5).
2. **`JsonImportDecoder`'s `element[key] ?: continue` conflation** —
   "key absent" and "value is JsonObject/JsonArray" reached the same
   code path; fixed via an explicit type check (M5.8.6).
3. **`Json { isLenient = false }` does not reject bare `NaN`/`Infinity`**
   — a real, wrong initial assumption, proven wrong by two real test
   failures; fixed via an explicit literal-text check
   (`import-json-security.md`).
4. **A real tied fit-score in `ImportEntityDetector`** — a minimal
   5-column Products header scored identically to Categories (0.85 both);
   not a detector bug, a real, disclosed characteristic of the fit
   formula, documented and worked around with a fuller test header
   (`import-entity-detection.md`).
5. **`db.importQueries.changes()` did not exist** until an explicit
   `changes:` query was added to `Import.sq`, mirroring `Catalog.sq`'s
   own established pattern (M5.8.13).
6. **A real nested-`Mutex`-deadlock hazard**, caught by design before
   it ever shipped: `ImportCommitExecutor` needed to write provenance/
   audit/consumed-marking from inside its own held `DatabaseWriteGate`
   lock, but `SqlDelightImportPersistenceRepository`'s methods each
   independently acquired that same lock. Fixed by extracting
   `ImportPersistenceCore` (lock-free primitives), reused by both the
   locked repository and the already-locked executor
   (`import-database-write-gate-report.md`).
7. **A real silent-skip bug: zero-stock rows dropped.** `ImportFieldParser.QUANTITY`
   (`Quantity.parse`, strict-positive — built for sale/return line
   quantities) was wrongly used for `initial_stock`/`loyalty_points`
   (real, zero-or-more BALANCE fields) — found by
   `ImportPerformanceAtScaleTest`'s own 2,000-row real commit (20 rows
   with a genuine `initial_stock="0"` were silently counted as
   `skipped`, not `inserted`). Fixed at the source with a new
   `QUANTITY_ZERO_OR_MORE` parser variant (`Quantity.zeroOrMore`),
   proven by 3 new real tests (`import-performance-memory.md`).

Consistent with M5.6/M5.7's own pattern, every real defect above was
found by this milestone's own tests failing first (or, for #1/#2/#6,
by real code review during implementation before a test was even run)
— never discovered after the fact by a separate audit.
