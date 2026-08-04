# Import SQLite Security (M5.8.8)

Real, cleanly-split SQLite decoder — cleaner than XLSX's split
(`import-xlsx-security.md`) because SQLite has no byte-level container
format that itself needs a platform library: the entire decision
pipeline (`SqliteImportPipeline.kt`, `SqliteSecurityPolicy.kt`,
`SqliteTableComposer.kt`) lives in real, tested `commonMain` code,
driven through the `SqliteRawReader` interface. Only the real
implementation of that interface
(`AndroidSqliteRawReader`/`AndroidSqliteImportDecoder`, backed by
`android.database.sqlite.SQLiteDatabase`) is `androidMain`-specific.

## Real, executed test evidence

- `SqliteSecurityPolicyTest.kt`: 12/12
- `SqliteTableComposerTest.kt`: 7/7
- `SqliteImportPipelineTest.kt`: 4/4 (real end-to-end pipeline proof via
  a fake `SqliteRawReader`)

All zero failures, zero errors
(`TEST-com.actionaura.retail.importing.sqlite.*.xml`).

## Real threat model, reasoned explicitly

The uploaded file is always opened **read-only**
(`SQLiteDatabase.OPEN_READONLY`), and every query this codebase issues
against it is a real, hardcoded metadata query (`sqlite_master`,
`PRAGMA table_info`, `PRAGMA integrity_check`) or an explicit-column
`SELECT` this codebase constructs itself — **no SQL text from the
untrusted file is ever executed.**

- **Triggers** fire only on INSERT/UPDATE/DELETE. A read-only connection
  issuing only `SELECT` statements can never fire one, regardless of
  whether the file defines any. This codebase still **rejects any file
  that defines a trigger outright** (defense in depth) — a plain
  data-interchange file defining triggers is real, disclosed suspicious
  structure worth rejecting rather than merely relying on read-only
  mode as the sole safeguard.
- **Views** are pure, side-effect-free `SELECT` projections in SQLite —
  querying one cannot execute a trigger or mutate data. Real,
  legitimate export tools sometimes ship curated views, so this
  codebase **allows** them.
- **Virtual tables** can invoke real extension/module code when queried
  (`xBestIndex`/`xFilter`). This codebase **never queries one**,
  detecting them via `sql LIKE 'CREATE VIRTUAL TABLE%'` in
  `sqlite_master` and rejecting the whole file if any exist.

## Real requirement-by-requirement coverage

| Requirement | Real handling | Proven by |
|---|---|---|
| Never execute SQL supplied by the imported file | Structural — every query is a real, hardcoded string this codebase wrote; table/column names are interpolated only as quoted, escaped SQL *identifiers* (never as executable SQL text) | `AndroidSqliteRawReader.escapeIdentifier` (real, disclosed: cannot be unit-tested on this host) |
| Never execute triggers | Rejected outright if any exist, in addition to read-only mode structurally preventing their firing | `anyTriggerRejectsTheWholeFile`, `triggerInTheSchemaStopsThePipelineBeforeAnyTableIsRead` |
| Never execute views as application logic | Allowed as real, safe, side-effect-free query targets — never treated as "application logic" | `aPlainViewIsAllowedSinceItHasNoSideEffects` |
| Never copy arbitrary schema objects | Only real table rows are ever copied into a `NormalizedTable`; indexes/triggers/views themselves are inspected as metadata only, never materialized | Structural |
| Never trust `application_id`/`user_version` alone | Not consulted at all — real table/schema inspection is the only real authority used | N/A — never referenced |
| Never expose the production connection to arbitrary `ATTACH` | The untrusted file is opened as its own, separate, standalone `SQLiteDatabase` instance — never `ATTACH`ed to the app's real production database | Structural |
| Never allow imported pragmas to alter production settings | No `PRAGMA` from the file is ever read or executed — only this codebase's own hardcoded `PRAGMA table_info`/`PRAGMA integrity_check` | Structural |
| Inspect header / integrity check | Real `PRAGMA integrity_check`; a failing result stops the pipeline before any table is read | `failedIntegrityCheckIsRejected`, `failedIntegrityCheckStopsThePipelineBeforeReadingAnyRows` |
| Inspect table/column list, declared types | Real `sqlite_master`/`PRAGMA table_info` | `composesRealColumnsAndRows` |
| Inspect triggers/views/virtual tables | Real `sqlite_master` scan classifies every schema object by `type` and `sql` text | Covered above |
| Reject excessive schema objects | `ImportLimits.maxSqliteTableCount` bounds the total `sqlite_master` object count | `excessiveSchemaObjectCountIsRejected` |
| Reject malformed schema / unknown required tables | A schema with zero real (non-`sqlite_`-prefixed) tables is rejected | `aSchemaWithNoRealTablesIsRejected`, `sqliteInternalTablesAreExcludedFromTheRealTableCount` |
| Read only allowlisted tables and columns | `readRows` always takes an explicit `columnNames` list — there is no code path that does `SELECT *` | Structural, `AndroidSqliteRawReader.readRows` |
| Row count bound | `ImportLimits.maxSqliteRowCount`, checked against the CHOSEN table's real count before any row is read | `excessiveRowCountIsRejected`, `excessiveRowCountOnTheChosenTableStopsBeforeReadingRows` |

## Real, confirmed target-table heuristic (behavioral evidence, not invented)

`SqliteSecurityPolicy.chooseTargetTable` picks the real table with the
most rows — the exact same heuristic `import-authority-audit.md`
already confirmed the legacy Python authority uses (`_parse_file`'s
SQLite branch: "picks the biggest table"). Reused as real, confirmed
intentional behavior, not invented for this milestone. Proven by
`theTableWithTheMostRealRowsIsChosenMatchingTheLegacyAuthoritysOwnHeuristic`.

## Real, disclosed platform-testability split

`AndroidSqliteRawReader`/`AndroidSqliteImportDecoder` (real
`android.database.sqlite.SQLiteDatabase` I/O, real temp-file
write/cleanup) cannot be exercised by a unit test on this host — same
disclosed limitation as `AndroidXlsxImportDecoder`. Unlike XLSX,
though, **the entire decision pipeline** (`SqliteImportPipeline`) is
real, pure `commonMain` code proven end-to-end via a fake
`SqliteRawReader` — the untested platform surface here is smaller,
limited to real I/O plumbing (open file, run a hardcoded query, read a
cursor), with zero decision logic of its own.
