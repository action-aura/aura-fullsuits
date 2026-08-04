# Import Domain Value Parsing (M5.8.10)

Real domain value parsing (`ImportDomainValueParser.kt`), proven by
`ImportDomainValueParserTest.kt` (14/14,
`TEST-com.actionaura.retail.importing.entity.ImportDomainValueParserTest.xml`
tests="14" failures="0" errors="0").

## No import-only financial parsing

Every `Money`/`Quantity` value is parsed through the exact same real M3
canonical parsers (`Money.parse`/`Quantity.parse`, returning
`FinancialResult`) every other part of this codebase uses — proven by
`exactMoneyParsesThroughTheRealM3Parser`/
`exactQuantityParsesThroughTheRealM3Parser`. There is no hand-rolled
`_parse_number`-equivalent in this codebase (the legacy authority's own
`_parse_number` — plain `float()` after regex-stripping — is explicitly
NOT ported, since it is exactly the class of unsafe financial parsing
this milestone must not repeat).

## `PercentageRate.trusted` wrapped safely at the import boundary

`PercentageRate` has no `parse`-with-`Result` API (only `trusted`,
which throws on malformed input, and `clampToDiscountRange`, which is
specific to discount percentages, not tax rate). Real fix: the import
parser wraps `PercentageRate.trusted(String)` in a `try/catch`,
converting a real thrown exception into a real
`ImportValidationIssue.UnparseableValue` — proven by
`malformedPercentageRateIsARealValidationIssueNeverThrown`. This is a
safe usage of the existing API at the import boundary, not a
modification to `PercentageRate` itself (out of scope).

## Never coerced to zero/empty on failure

Every unparseable value produces a real `ImportValidationIssue`, never
a silently-substituted zero or empty value — proven across Money,
Quantity, PercentageRate, Integer, and Email parsing
(`malformedMoneyIsARealValidationIssueNeverCoercedToZero` and its
per-type siblings). A blank/absent raw cell is the one real, valid
"empty" case (`ImportParsedValue.Empty`), distinct from a genuinely
malformed non-blank value.

## Leading zeros never stripped

`TEXT`-parsed fields (including barcode/SKU, both mapped as `TEXT`
per `ImportEntitySchemas`) are never coerced through a numeric type —
proven by `leadingZeroBarcodeTextIsPreservedExactly`.

## Negative Quantity is a real validation issue, not silently clamped

`Quantity.parse` (the real M3 parser) rejects a negative value —
proven here at the import boundary by
`negativeQuantityIsARealValidationIssue`, confirming the import layer
surfaces that rejection as a real issue rather than working around it.

## Ambiguous locale number formats

M5.8.10's own required case (`"1,234"` — is it 1,234 or 1.234?) is
explicitly **not re-decided at the import-decoder layer** — the raw
cell text reaches `Money.parse` unmodified, and that function's own
documented contract (`financial-invariant-catalog.md`) is the single
real authority for the accept/reject decision, proven not to throw or
silently produce a dual result by
`ambiguousLocaleNumberFormatIsHandledByTheRealM3ParserNotGuessedHere`.
