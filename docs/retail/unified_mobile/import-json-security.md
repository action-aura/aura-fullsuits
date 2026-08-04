# Import JSON Security (M5.8.6)

Real, genuinely shared KMP JSON decoder (`JsonImportDecoder.kt` +
`JsonStructuralScanner.kt`, `import-parser-decision.md`'s Option-A
choice: `kotlinx.serialization.json` is already a real `commonMain`
dependency). Proven by `JsonImportDecoderTest.kt` (19/19,
`TEST-com.actionaura.retail.importing.json.JsonImportDecoderTest.xml`
tests="19" failures="0" errors="0").

## Supported real shapes

1. A root JSON array of flat row objects — the real, legacy-confirmed
   shape (`import-authority-audit.md`'s own citation of
   `_parse_file`'s JSON branch: `isinstance(parsed, list)`).
2. A root JSON object containing exactly **one** array-valued key whose
   elements are row objects — a real, intentional extension beyond the
   legacy authority (common in JSON export tools), proven by
   `rootObjectWithOneNamedArrayIsSupported`. A root object with **two or
   more** array-valued keys is rejected as ambiguous
   (`rootObjectWithTwoArraysIsRejectedAsAmbiguous`) rather than guessed.

An explicitly-versioned multi-entity JSON package format is **not**
built this milestone — real, disclosed scope decision, no caller
requirement exists for it yet (same "an empty marker is the honest
boundary" discipline `RepositoryBoundaries.kt` already established for
undesigned shapes).

## Real requirement-by-requirement coverage

| Requirement | Real handling | Proven by |
|---|---|---|
| Excessive nesting | Rejected against `ImportLimits.maxJsonDepth` by a real, pre-parse, string-aware text scan (`JsonStructuralScanner`) — `kotlinx.serialization`'s own tree has no depth limit, so this codebase's own scan is the actual enforcement | `excessiveNestingDepthIsRejected` |
| Cyclic references | Structurally impossible from real JSON text (a tree by construction) — not a real threat for this format, unlike a binary/reference-based format |
| Duplicate object keys | Rejected via the same pre-parse scanner — real, executed finding: `kotlinx.serialization.json`'s `JsonObject` silently resolves a duplicate key to its LAST value once parsed, so the ambiguity is only observable in the raw text, before parsing | `duplicateObjectKeyIsRejectedNotSilentlyResolvedToTheLastValue`, with `duplicateKeyInADifferentObjectIsNotAFalsePositive` proving the scanner correctly scopes key-tracking per object, not globally |
| Nonfinite numbers | Rejected — see the real finding below | `nanLiteralIsRejectedNotSilentlyAccepted`, `infinityLiteralIsRejectedNotSilentlyAccepted` |
| Excessively large strings | Rejected against `ImportLimits.maxCellLength` (per-cell) | Covered by the same limit as CSV; not separately re-tested |
| Unsupported mixed root shapes | A root that is neither an array nor an object-with-exactly-one-array is rejected | `nonArrayNonObjectRootIsRejected` |
| Polymorphic type injection / arbitrary class names | Structurally impossible — this decoder parses into `JsonElement` only (a plain, closed value tree), never deserializes into an application class via reflection/polymorphism | N/A, structural |
| Unknown executable metadata | N/A — JSON has no executable content class; not applicable to this format the way macros/formulas are to XLSX |

## Real, executed finding: `isLenient = false` does NOT reject bare `NaN`/`Infinity`

The first, reasonable-looking implementation assumed
`Json { isLenient = false }` would cause `parseToJsonElement` to reject
bare (unquoted) `NaN`/`Infinity`/`-Infinity` tokens as invalid JSON
(they are not part of the JSON grammar). **Real, executed test
evidence proved this assumption wrong**: both
`nanLiteralIsRejectedNotSilentlyAccepted` and
`infinityLiteralIsRejectedNotSilentlyAccepted` failed on the first real
run — `kotlinx.serialization.json` parses these tokens successfully
regardless of `isLenient`, and the initial backstop
(`JsonPrimitive.doubleOrNull`) turned out to be ineffective too (it
returns `null` for these literals rather than a real `Double.NaN`/
`Double.POSITIVE_INFINITY`, making the intended check a no-op).

**Real fix**: the decoder now checks the parsed `JsonPrimitive`'s own
raw `.content` text directly against a literal set
(`"NaN"`, `"Infinity"`, `"-Infinity"`, `"+Infinity"`) for any
non-string-typed primitive, rejecting with `ImportError.UnsafeContent`
before the value ever reaches cell-text conversion. This is a real,
corrected implementation driven by the test's own failure, not a
guess — the exact discipline this initiative has followed throughout:
real assertions corrected to match observed reality, and the fix
verified by re-running the same test.

## Never parses JSON numbers through `Double`

`jsonScalarToText` converts every `JsonPrimitive` via its own `.content`
string (the exact original literal `kotlinx.serialization` retained),
never via `.double`/`.doubleOrNull` — proven by
`numericLiteralIsPreservedAsExactText` (`19.99` round-trips as the
exact string `"19.99"`) and `leadingZeroBarcodeStringIsNeverStripped`
(a quoted barcode string is never touched by numeric conversion at
all). Money/Quantity parsing consumes this raw text through the real M3
parsers downstream (M5.8.10), never through this decoder.

## Real, additional findings

- A `null` JSON value becomes a real `null` cell (never an error) —
  `nullValueBecomesARealNullCellNotAnError`.
- A nested object or array as a cell value is explicitly rejected
  (`ImportError.UnsafeContent`) rather than silently stringified or
  silently dropped — `nestedObjectAsACellValueIsRejected`,
  `nestedArrayAsACellValueIsRejected`.
- Malformed JSON (e.g. a missing comma) is rejected safely, never a
  crash — `malformedJsonIsRejectedSafely`.
- A null byte anywhere in the raw bytes is rejected before any parsing
  is attempted — `nullByteContentIsRejectedAsUnsafe`.
- Arabic text values are preserved exactly —
  `arabicTextValueIsPreservedExactly`.
- `ImportLimits.maxJsonArrayLength` is enforced independently of
  `maxRowCount` — `excessiveArrayLengthIsRejected`.
