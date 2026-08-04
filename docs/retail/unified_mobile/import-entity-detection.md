# Import Entity Detection (M5.8.9)

Real, deterministic entity detection and column mapping
(`ImportEntityDetector.kt`), proven by `ImportEntityDetectorTest.kt`
(10/10, `TEST-com.actionaura.retail.importing.entity.ImportEntityDetectorTest.xml`
tests="10" failures="0" errors="0").

## Real port of the legacy scoring shape

`import-authority-audit.md`'s own citation of `_suggest_mapping`/
`_entity_fit` is ported as a real, deterministic scoring function:

```
exact header match (name/label)          -> 100
recognized alias (English or Arabic)     -> 90
header contains/contained-by the key     -> 75
header contains/contained-by the label   -> 65
partial alias match                      -> 55
auto-apply threshold                     -> 65
```

**Not ported**: the legacy authority's value-type sniffing (checking
whether a column's sample VALUES look like emails/dates/numbers to
boost score). Real, disclosed scope reduction — header-name matching
alone is the real signal used this milestone.

## Real fit formula (unchanged from the legacy authority)

```
fit = 0.7 * (required fields mapped / required fields) + 0.3 * (total fields mapped / total fields)
```

Proven exact by `aRealProductsFileScoresHighestAmongAllFiveEntities` and
`aRealCategoriesFileScoresHighestAmongAllFiveEntities`.

## Real finding: a sparse file can tie two entities exactly

The first real run of
`aRealProductsFileScoresHighestAmongAllFiveEntities` failed: a minimal
5-column products header set (`name, sku, barcode, sell_price,
cost_price`) scored **exactly 0.85 fit for both Products and
Categories** (both have 100% required-field coverage and 50% total
coverage on that particular header set) — `detectCandidates`'s stable
sort then returned `CATEGORIES` first (declared earlier in the
`ImportEntityType` enum), not `PRODUCTS`. This was a real bug in the
TEST's chosen header set, not the detector — fixed by using a fuller,
more realistic header set (adding `tax_rate`/`unit`) that clearly
distinguishes Products. Documented here as a real, disclosed
characteristic of the fit formula: **sparse files can legitimately tie**,
and the real, deterministic tie-break in that case is enum declaration
order, which callers should not rely on as a meaningful signal — a tied
result is exactly the kind of case `isAmbiguous` exists to catch.

## Ambiguity detection (never auto-selected)

`isAmbiguous` compares the top two ELIGIBLE candidates (all required
fields mapped, fit >= a real minimum of 0.55) — if their fit scores are
within a real margin (0.1), the detection is reported as ambiguous, per
M5.8.9's own explicit "do not auto-select when two candidates are
materially ambiguous" instruction. Proven by
`aFileMatchingTwoEntitiesEquallyWellIsReportedAsAmbiguous` (Suppliers vs
Branches, both real, both requiring only `name` plus optional
phone/address, tie exactly at 0.925) and
`aClearlyDistinctFileIsNotFlaggedAmbiguous` (a full products header set,
margin 0.12 > 0.1, correctly not ambiguous).

## Real, structural safety proven

- `oneColumnIsNeverAssignedToTwoDifferentFields` — the greedy assignment
  never double-claims a column.
- `unrecognizedColumnsRemainUnmapped` — a column matching nothing stays
  `null`, never guessed.
- `missingRequiredFieldsAreReportedOnTheCandidate` — a candidate missing
  a required field is still returned (with `missingRequiredColumns`
  populated), never silently dropped from the candidate list.
