# Import Security Test Matrix (M5.8.23)

Real, cross-referenced coverage review against the checkpoint's own
required security test matrix (format security, limits at boundary
values, domain parsing, dry-run, commit). Every row below cites a real,
executed test file — no row is asserted without a real test backing it.

## Format security

| Concern | Real test file | Status |
|---|---|---|
| CSV null-byte/malformed-encoding rejection | `CsvImportDecoderTest.kt` | Covered |
| CSV RFC4180 quoting/embedded-delimiter/embedded-newline | `CsvImportDecoderTest.kt` | Covered |
| JSON depth-bomb / duplicate-key rejection | `JsonStructuralScanner` via `JsonImportDecoderTest.kt` | Covered |
| JSON NaN/Infinity literal rejection | `JsonImportDecoderTest.kt` (real bug found+fixed, `import-json-security.md`) | Covered |
| XLSX zip-bomb (expansion ratio + cumulative size) | `XlsxSecurityPolicyTest.kt` (pure) + `AndroidXlsxImportDecoderTest.kt` (real genuine-inflate, M5.8.20) | Covered |
| XLSX macro/external-link rejection | `XlsxSecurityPolicyTest.kt` | Covered |
| XLSX formula-cell rejection (never trust cached `<v>`) | `XlsxSheetXmlReaderTest.kt`/`XlsxTableComposerTest.kt` | Covered |
| XLSX path-traversal entry names | `XlsxSecurityPolicyTest.kt` | Covered |
| SQLite read-only, trigger/virtual-table rejection | `SqliteSecurityPolicyTest.kt` | Covered |
| SQLite integrity-check gate | `SqliteSecurityPolicyTest.kt` | Covered |
| Format detection: magic bytes as real authority, extension as weak metadata | `ImportFormatDetectorTest.kt` | Covered |

## Limits at boundary values

| Concern | Real test | Status |
|---|---|---|
| `maxRowCount`: exactly-at-limit succeeds, one-over rejected | `CsvImportDecoderTest.rowCountExactlyAtTheLimitSucceedsOnlyOneOverIsRejected` (M5.8.23, new) | Covered |
| `maxCellLength`: exactly-at-limit succeeds, one-over rejected | `CsvImportDecoderTest.cellLengthExactlyAtTheLimitSucceedsOnlyOneOverIsRejected` (M5.8.23, new) | Covered |
| `maxColumnCount`: exactly-at-limit succeeds, one-over rejected | `CsvImportDecoderTest.columnCountExactlyAtTheLimitSucceedsOnlyOneOverIsRejected` (M5.8.23, new) | Covered |
| `maxCompressedFileSizeBytes` rejection | `CsvImportDecoderTest.kt`, `PickedFileImportSourceTest.kt` | Covered |
| `maxJsonArrayLength` rejection | `JsonImportDecoderTest.kt` | Covered |
| Negative pagination/report limits (reporting, not import — real, pre-existing precedent for the "boundary, not just over-limit" discipline this matrix follows) | `ReportingBoundsTest.kt` (M5.7) | Precedent |

## Domain parsing

| Concern | Real test | Status |
|---|---|---|
| Money/Quantity/PercentageRate through canonical M3 parsers only, never `Double` | `ImportDomainValueParserTest.kt` | Covered |
| Strict-positive vs zero-or-more Quantity distinction | `ImportDomainValueParserTest.kt` (real bug found+fixed, M5.8.21) | Covered |
| Malformed email rejected, not silently accepted | `ImportDomainValueParserTest.kt`, `ImportCommitExecutorMatrixTest.aMalformedCustomerEmailSkipsThatRowOnlyNeverTheWholeImport` (M5.8.23) | Covered |
| Blank/absent value is real `Empty`, never an error | `ImportDomainValueParserTest.kt` | Covered |

## Dry-run

| Concern | Real test | Status |
|---|---|---|
| Immutability (no field ever updated post-creation, except one-time `consumedAt`) | `SqlDelightImportPersistenceRepositoryTest.kt` | Covered |
| Cross-business isolation (dry-run scoped by companyId) | `SqlDelightImportPersistenceRepositoryTest.aDryRunFromAnotherCompanyIsNeverReturned` | Covered |
| Expiry enforcement | `ImportCommitRevalidatorTest.anExpiredDryRunFailsWithDryRunExpired` | Covered |

## Commit

| Concern | Real test | Status |
|---|---|---|
| Bounded token, server-side revalidation (8 real checks) | `ImportCommitRevalidatorTest.kt` (9/9) | Covered |
| Cross-company token resolves to not-found, not a later rejection | `ImportCommitRevalidatorTest.aTokenClaimingADifferentCompanyThanTheRealDryRunNeverSucceeds` | Covered |
| Real transactional all-or-nothing, driver-injected rollback | `ImportCommitExecutorTest.kt` (3 real rollback tests, M5.8.15/16) | Covered |
| Idempotent retry after real success never double-applies | `ImportCommitExecutorMatrixTest.retryingTheSameCommitAfterARealSuccessIsRejectedNeverDoubleApplied` (M5.8.23, new) | Covered |
| Cross-business isolation at commit time (name-match never crosses companies) | `ImportCommitExecutorMatrixTest.importingForOneCompanyNeverMatchesOrTouchesAnotherCompanysRowWithTheSameName` (M5.8.23, new) | Covered |
| Unexpected idempotency-key collision aborts, never mis-reports success | `ImportCommitExecutorTest.anUnexpectedIdempotencyKeyCollisionAbortsAndRollsBackRatherThanMisreportingSuccess` | Covered |
| `DatabaseWriteGate` real concurrency (two commits never interleave) | `ImportCommitExecutorTest.twoConcurrentCommitsAgainstTheSameDatabaseAreRealSerializedByTheGateNeverInterleaved` | Covered |

## Real, disclosed gaps NOT covered (out of scope, explicitly)

- **Real device-level Android SAF/content-provider behavior** — no
  device/emulator on this host (`import-android-adapter-validation.md`).
- **Real `SQLiteDatabase`-backed SQLite import decoder execution** —
  same real constraint (`AndroidSqliteImportDecoder`/`AndroidSqliteRawReader`).
- **Full legacy `FIELD_ALIASES` parity** — a real, curated subset was
  chosen deliberately (`import-entity-detection.md`'s own disclosed
  scope reduction), not a gap in THIS matrix's own stated scope.
