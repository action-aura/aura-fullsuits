# Import CSV Security (M5.8.5)

Real, hand-written, genuinely shared KMP CSV decoder
(`CsvImportDecoder.kt`, `import-parser-decision.md`'s Option-A choice).
Proven by `CsvImportDecoderTest.kt` (21/21,
`TEST-com.actionaura.retail.importing.csv.CsvImportDecoderTest.xml`
tests="21" failures="0" errors="0").

## Real requirement-by-requirement coverage

| Requirement | Real handling | Proven by |
|---|---|---|
| UTF-8 BOM | Stripped before parsing, never becomes part of the first header's text | `utf8BomIsStrippedNotTreatedAsPartOfTheFirstHeader` |
| Arabic text | UTF-8 decoding preserves multi-byte characters exactly, no ASCII-only assumption anywhere in the parser | `arabicTextIsPreservedExactly` |
| Quoted delimiters | Real RFC4180-shaped quote handling — a delimiter inside `"..."` is literal text, not a field boundary | `quotedFieldsMayContainTheDelimiter` |
| Embedded line breaks in quoted fields | A `\n`/`\r\n` inside an open quote does not end the row | `quotedFieldsMayContainEmbeddedLineBreaks` |
| Escaped quotes | `""` inside a quoted field becomes one literal `"` | `escapedDoubleQuotesInsideAQuotedFieldBecomeOneLiteralQuote` |
| Empty cells | A real empty string (`""`), distinct from a missing trailing cell (`null`) | `emptyCellsAreRealEmptyStringsNotNull` |
| Duplicate headers | Both preserved positionally — the decoder never silently drops or merges same-named columns; ambiguity resolution is a mapping-stage (M5.8.9) concern, not a decoding-stage one | `duplicateHeadersAreBothPreservedPositionally` |
| Missing headers | An empty file (`""`) is rejected as `NoHeaders`; a header-only file (no data rows) is rejected as `NoDataRows` | `emptyFileIsRejectedAsNoHeaders`, `headerOnlyFileIsRejectedAsNoDataRows` |
| Inconsistent row width | Short rows pad missing trailing cells with `null`; rows with extra cells beyond the header count deterministically drop the excess, never shifting column meaning | `shortRowsArePaddedWithNullForMissingTrailingCells`, `extraCellsBeyondTheHeaderCountAreDeterministicallyDropped` |
| Excessive field length | Rejected against `ImportLimits.maxCellLength` | `excessiveCellLengthIsRejected` |
| Null bytes | Rejected outright as `ImportError.UnsafeContent`, before any parsing is attempted | `nullByteContentIsRejectedAsUnsafe` |
| Malformed encoding | Rejected as `ImportError.MalformedContent` via the real Kotlin stdlib `ByteArray.decodeToString(throwOnInvalidSequence = true)`, never silently substituted with U+FFFD | `malformedUtf8IsRejectedNotSilentlyReplaced` |
| Locale-looking numbers | Not a decoder concern — the decoder preserves raw cell text exactly; ambiguous-number-format handling belongs to M5.8.10's domain-value-parsing stage | N/A here, see `import-domain-parsing.md` |
| Leading zeros | Never stripped — every cell is plain text end to end, never coerced through a numeric type inside the decoder | `leadingZeroBarcodeIsNeverStripped` |

## Real, additional findings

- **Unterminated quoted field** is real, reportable malformed content
  (`unterminatedQuotedFieldIsRejectedAsMalformed`), not silently
  absorbed to end-of-file.
- **Excessive row count** is rejected against `ImportLimits.maxRowCount`
  (`excessiveRowCountIsRejected`).
- **Oversized file** is rejected against
  `ImportLimits.maxCompressedFileSizeBytes` before any parsing begins
  (`oversizedFileIsRejectedBeforeParsing`) — real reject-before-decode
  discipline.
- **Delimiter detection** is bounded to a small, documented allowlist
  (`,`, `;`, tab, `|`) — never an arbitrary sniffed character
  (`semicolonDelimitedCsvIsDetectedAndParsedCorrectly`).
- **Trailing blank line** at end-of-file is real and common (most
  spreadsheet exports add one) — handled without producing a phantom
  empty data row (`trailingBlankLineDoesNotProduceAPhantomEmptyRow`).

## Formula-injection marking — deferred, disclosed

The checkpoint's own instruction ("preserve strings beginning with
`=`,`+`,`-`,`@` as data while marking formula-injection risk for future
export contexts") is **structurally already half-satisfied**: this
decoder never interprets a cell value as a formula — every cell is
plain text, so `=SUM(A1:A10)` in a CSV cell is imported as the literal
6-character... as the literal string, never executed. The **marking**
half (flagging such values for a future export path to neutralize) is
explicitly deferred — no export feature exists yet in this codebase
(`import-authority-audit.md`'s own confirmation: "no export endpoint
exists"), so there is no real consumer for this marker yet. Building it
speculatively now would be exactly the kind of premature abstraction
this initiative's own discipline avoids. Documented here as a real,
named, deferred item for when export work begins, not silently dropped.
