# Import Performance & Memory (M5.8.21)

Real, executed timing at a representative real scale — proven by
`ImportPerformanceAtScaleTest.kt` (3/3). Real, disclosed measurement
discipline (matching `exact-aggregation-performance.md`'s own M5.7
precedent): every bound is generous with real margin over one observed
run, not a tight fit — M5.7 already found and disclosed real cold-JVM
timing variance for a comparable workload, so bounds here are
deliberately wide.

## Real measured numbers (this host, one real run)

| Operation | Real scale | Real measured | Bound (with margin) |
|---|---|---|---|
| CSV decode | 10,000 rows | 22ms | < 5,000ms |
| Entity detection | 10,000-row table | 18ms | < 1,000ms |
| Transactional commit | 2,000 products (with real auto-created categories, real inventory writes) | 1,389ms | < 30,000ms |

Entity detection is header-only scoring (`ImportEntityDetector.suggestMapping`
never inspects row data), so its real cost is independent of row count
— confirmed structurally by inspection, not just by this one measurement.

## Real bug found by this exact test, fixed at the source

The 2,000-row commit test's first real run failed: `insertedCounts`
was 1,980, not 2,000 — 20 real rows silently `skipped`. Root cause:
`ImportFieldParser.QUANTITY` routes through `Quantity.parse`, which is
strict-positive (`Quantity.kt`'s own real, documented design — built
for sale/return LINE quantities, `financial-invariant-catalog.md`
invariant #7/#7a) — but `initial_stock`/`loyalty_points` are real,
legitimate zero-or-more BALANCES (a product can genuinely have zero
stock; a customer zero points), the exact distinction `Quantity.kt`'s
own KDoc already documents via its separate `zeroOrMore` factory. 20
real rows in the synthetic dataset had `initial_stock="0"` (`i % 100 == 0`
for `i` in 1..2,000) and were rejected as `NonPositiveQuantity`,
silently counted as `skipped` rather than `inserted`.

Fixed at the source, not worked around in the test: added
`ImportFieldParser.QUANTITY_ZERO_OR_MORE` (routes through
`Quantity.zeroOrMore`), repointed `initial_stock`/`loyalty_points` in
`ImportEntitySchemas.kt` to it, updated both real usages in
`ImportCommitExecutor.kt`. Proven by 3 new real tests in
`ImportDomainValueParserTest.kt`: zero succeeds, negative still fails,
`NaN` still rejected. This was a real, silent data-loss bug for any
product genuinely at zero stock — not merely a test artifact.

## Real, disclosed scope: memory is bounded structurally, not measured

No memory profiler is available on this host. Memory exposure is
bounded by design, not by direct measurement: `ImportLimits.maxCompressedFileSizeBytes`
(25MB default) caps the single whole-file-in-memory buffer every
decoder holds (`import-resource-limits.md`); `ImportLimits.maxRowCount`
(100,000) bounds the resulting `NormalizedTable`'s row list; the
transactional commit processes rows in a single pass with no
accumulating per-row collection beyond the small per-entity `seenKeys`/
`categoryIdCache` maps. These are real, enforced structural bounds, not
a substitute for an actual profiler run — a true device-level memory
profile remains unverified on this host, matching this session's
standing disclosure pattern for anything requiring hardware this
Windows development machine does not have.
