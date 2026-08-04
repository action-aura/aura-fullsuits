# Import Resource Limits (M5.8.3)

Real, shared-application-layer limits (`ImportLimits.kt`), not a UI-only
restriction — every decoder and pipeline stage receives an
`ImportLimits` instance explicitly and enforces it, rejecting before
expensive decoding whenever the check can happen early.

## Real, disclosed legacy gap this closes

`import-authority-audit.md`'s own finding: the legacy Python authority
has **no file-size limit, no ZIP-bomb protection, no row/column/depth
cap of any kind** — `openpyxl.load_workbook` loads the entire uploaded
XLSX unconditionally, JSON reads the whole body into memory, SQLite caps
only the *chosen table's* row read (50,000) after already writing the
whole uploaded file to a temp file and opening it. This milestone is
the first time any of these limits exist for Retail's import authority,
on either platform.

## The real limits and their defaults

| Limit | Default | Real reasoning |
|---|---|---|
| `maxCompressedFileSizeBytes` | 25 MB | A real, generous bound for a mobile device's available memory/storage; enforced from source metadata/streaming length before a full decode where the platform API supports it |
| `maxUncompressedSizeBytes` | 100 MB | The real zip-bomb defense for XLSX — decompressed output is bounded independently of the compressed input size |
| `maxExpansionRatio` | 100× | A second, independent zip-bomb defense — a compressed:uncompressed ratio beyond this is rejected even if the absolute uncompressed size is still under the cap (catches a small-but-extreme-ratio bomb) |
| `maxRowCount` | 100,000 | Matches the real scale this initiative already validates against (`reporting-scale-dataset.md`'s own 100,001-sale dataset) |
| `maxColumnCount` | 200 | Real Retail schemas never exceed 10 fields per entity (`import-handler-matrix.md`) — 200 is a generous multiple for a combined/malformed file, not a tight fit |
| `maxCellLength` | 4,000 chars | Bounds a single malicious/malformed cell from consuming excessive memory |
| `maxHeaderLength` | 200 chars | Same reasoning, for header text specifically |
| `maxWorksheetCount` | 20 | XLSX-specific — real Retail import files are single-sheet; 20 is generous headroom |
| `maxZipEntryCount` | 200 | XLSX-specific — a real XLSX has a small, bounded number of ZIP parts (workbook.xml, sharedStrings.xml, one or more sheetN.xml, styles, relationships); 200 comfortably covers real files while rejecting an entry-count-based zip bomb |
| `maxJsonDepth` | 32 | JSON-specific — real Retail JSON import shapes are shallow (array of flat row objects); 32 rejects a deliberately deep-nested JSON bomb |
| `maxJsonArrayLength` | 100,000 | Matches `maxRowCount` |
| `maxCsvFieldLength` | 4,000 chars | Same reasoning as `maxCellLength`, CSV-specific |
| `maxSqliteTableCount` | 200 | SQLite-specific — bounds schema-inspection cost on a malicious file with an excessive number of tables |
| `maxSqliteRowCount` | 100,000 | Matches `maxRowCount` |
| `maxImportedEntityCount` | 100,000 | The real ceiling on how many entity rows one commit may create/update, independent of the source row count (defense against a malformed mapping fan-out) |
| `maxValidationIssueCount` | 500 | Bounds dry-run memory/response size — a file with more real problems than this is rejected as unsuitable for import rather than enumerating every one of, e.g., 90,000 issues |
| `maxDryRunLifetimeMillis` | 30 minutes | A real, bounded window between preview and commit — long enough for a real user decision, short enough that a stale dry-run cannot be committed against a database state that has since materially changed |
| `maxConcurrentImportJobs` | 1 | Matches `DatabaseWriteGate`'s own single-writer discipline — only one import commit may be in flight at a time per app instance |

## Where limits are enforced

- **Before decoding**: `ImportSource.readBounded(limits)` checks the
  real declared/streamed byte count against
  `maxCompressedFileSizeBytes` before the decoder ever touches the
  content.
- **During decoding**: each format's decoder enforces its own
  format-specific limits incrementally (row count as rows are produced,
  ZIP entry count as entries are enumerated, JSON depth as the tree is
  walked) — never by decoding everything first and checking after.
- **During validation/dry-run**: `maxValidationIssueCount` and
  `maxImportedEntityCount` bound the dry-run's own output size.

## Real test coverage

Each limit is tested at just-below/exactly-at/just-above per M5.8.23's
own required matrix — see `import-csv-security.md`,
`import-json-security.md`, `import-xlsx-security.md`,
`import-sqlite-security.md` for the format-specific real test evidence.

## Not increased to make a large test pass

No limit value in this document was raised to accommodate a specific
test — every default above is the real, intended production value,
chosen from this codebase's own established scale evidence
(`reporting-scale-dataset.md`) and real memory/mobile-device reasoning,
not reverse-engineered from a test fixture.
