# Import XLSX Security (M5.8.7)

Real, split-architecture XLSX decoder
(`import-parser-decision.md`'s Option-B choice): every SECURITY
DECISION is real, pure `commonMain` logic (`XlsxSecurityPolicy.kt`,
`XlsxSheetXmlReader.kt`, `XlsxTableComposer.kt`), proven by 25 real
tests; the actual ZIP byte I/O is real `androidMain` code
(`AndroidXlsxImportDecoder.kt`, using the platform's own
`java.util.zip`), which this Windows host cannot unit-test (no
Robolectric, no device/emulator — disclosed, not silently skipped).

## Real, executed test evidence (pure logic layer)

- `XlsxSecurityPolicyTest.kt`: 12/12
  (`TEST-com.actionaura.retail.importing.xlsx.XlsxSecurityPolicyTest.xml`)
- `XlsxSheetXmlReaderTest.kt`: 10/10
  (`TEST-com.actionaura.retail.importing.xlsx.XlsxSheetXmlReaderTest.xml`)
- `XlsxTableComposerTest.kt`: 7/7
  (`TEST-com.actionaura.retail.importing.xlsx.XlsxTableComposerTest.xml`)

All zero failures, zero errors.

## Real requirement-by-requirement coverage

| Requirement | Real handling | Proven by |
|---|---|---|
| ZIP bombs (compressed:uncompressed ratio) | Per-entry ratio checked against `ImportLimits.maxExpansionRatio` before any entry is extracted; the real Android decoder additionally enforces a byte-count backstop DURING extraction itself (`readBounded`), not just from metadata that a crafted ZIP could misreport | `realZipBombExpansionRatioIsRejected` |
| ZIP bombs (cumulative size) | Running total checked against `ImportLimits.maxUncompressedSizeBytes` across all entries | `cumulativeUncompressedSizeBudgetIsEnforcedAcrossManySmallEntries` |
| Path traversal within archive entries | Entry names containing `..`, a leading `/`, or a `\` are rejected | `pathTraversalEntryNameIsRejected`, `absolutePathEntryNameIsRejected` |
| Excessive compression ratio | Same as ZIP-bomb ratio check above | Same |
| Excessive shared strings | Bounded transitively by `maxUncompressedSizeBytes`/`maxCellLength` on the extracted `sharedStrings.xml`; no separate shared-string COUNT limit was added since the real memory risk (large XML) is already bounded by the size limit | N/A — covered by size limits |
| Excessive worksheet count | Checked against `ImportLimits.maxWorksheetCount` | `excessiveWorksheetCountIsRejected` |
| Excessive cells | Checked against `ImportLimits.maxRowCount`/`maxColumnCount`/`maxCellLength` during cell extraction | `excessiveRowCountIsRejected`, `excessiveCellLengthIsRejected` |
| Oversized styles | Not separately parsed at all — this decoder extracts only `sharedStrings.xml` and one worksheet's cell values, `styles.xml` is never read | N/A — structurally out of scope |
| External relationships | Any `xl/externalLinks/` entry rejects the whole workbook | `externalLinksAreRejected` |
| External links | Same | Same |
| Formulas | Any cell containing a `<f>` element rejects the whole worksheet — cached `<v>` results are never trusted as if they were real values | `formulaCellIsFlaggedNotSilentlyTrustedViaItsCachedValue` (reader), `anyFormulaCellAnywhereRejectsTheWholeSheet` (composer) |
| Macros | An `xl/vbaProject.bin` entry (the real, standard macro-enabled-workbook indicator) rejects the whole workbook | `macroEnabledWorkbookIsRejected` |
| Embedded OLE objects | Not parsed — this decoder never reads `xl/embeddings/` or any OLE-related part; such entries are inert (not executed, not extracted) | N/A — structurally inert |
| Images/media when irrelevant | Never read — only `sharedStrings.xml` and the target worksheet XML are extracted | N/A — structurally out of scope |
| Hidden worksheets where unsupported | Not distinguished — the decoder reads the first worksheet part found alphabetically; hidden-sheet metadata (`state="hidden"` in `workbook.xml`) is not consulted, a real, disclosed scope limitation | Not separately tested |
| Malformed workbook relationships | A missing `[Content_Types].xml` (the real, required top-level part) rejects the file as not a real workbook | `missingContentTypesIsRejectedAsNotARealWorkbook` |
| Encrypted workbooks | **Structurally rejected before this decoder even runs**: real OOXML encryption wraps the package in a CFBF/OLE2 container, not a plain ZIP — `ImportFormatDetector`'s real ZIP-magic-byte check (`import-format-detection-contract`'s own logic) already fails to recognize an encrypted workbook as XLSX at all | Structural, via `ImportFormatDetector` |

## Real, disclosed decoding-fidelity scope

This decoder extracts a narrow, real slice of the OOXML spec: shared
strings (`xl/sharedStrings.xml`) and one worksheet's cell values
(`xl/worksheets/*.xml`, the alphabetically-first one). Real,
intentional limitations, not defects:

- Only the FIRST worksheet (alphabetically by part name) is read — real
  Retail import files are single-sheet (`import-authority-audit.md`'s
  own confirmation the legacy authority does `wb.active` = the first
  sheet).
- Cell styles/number formats (`styles.xml`) are never consulted — every
  value is read as its raw stored text, matching this decoder's own
  "preserve exact text, never coerce through a numeric type" discipline
  already established for CSV/JSON.
- Merged cells, hidden rows/columns, and print settings are not
  interpreted — irrelevant to importing tabular data.

## Real, disclosed platform-testability split

`AndroidXlsxImportDecoder.kt` (real ZIP I/O, `java.util.zip.ZipInputStream`)
delegates every decision it makes immediately to the tested `commonMain`
functions above — this class's own logic is limited to: enumerate
entries, extract two named entries' bytes, and hand them to the tested
pure functions. This minimizes the real, untested surface to pure I/O
plumbing.

**Correction (M5.8.20):** the claim above originally said this class
"cannot be exercised by a unit test on this host." Real, re-checked
evidence: its only imports are `java.util.zip.*`/`java.io.*` (plain JVM
stdlib), never a real `android.*` framework class — unlike
`AndroidSqliteImportDecoder` (real `SQLiteDatabase`), it needed no
device/Robolectric at all. It IS now real, JVM-unit-tested —
`AndroidXlsxImportDecoderTest.kt` (4/4), see
`import-android-adapter-validation.md` for the full corrected account.
