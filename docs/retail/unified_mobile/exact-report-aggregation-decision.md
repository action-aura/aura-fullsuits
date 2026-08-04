# Aura Retail Unified Mobile — Exact Report Aggregation Decision (M5.6.1)

## The question, and the governing rule

The shared schema stores money/quantity as TEXT specifically to avoid binary-float persistence (`database-schema-contract.md` rule 1). Does SQLite's `SUM()`/`AVG()` on that TEXT column silently reintroduce the exact problem the TEXT representation exists to avoid? Per the governing spec's own instruction: **assume unsafe until proven** — this is real, executed proof, not an assumption in either direction.

## Real, executed evidence (`SqliteTextMoneyAggregationTest.kt`, `./gradlew :shared:testDebugUnitTest`)

- `sumOnTextColumnCoercesToRealNotText` — `SELECT typeof(SUM(amount))` on a TEXT column returns `"real"`, never `"text"`. **Confirmed**: SQLite applies numeric affinity to `SUM()`'s argument regardless of the column's declared type — the result is unconditionally a binary-float `REAL`.
- `avgOnTextColumnAlsoCoercesToReal` — same confirmed for `AVG()`.
- `sumOfTwoValuesThatDoNotSumExactlyInBinaryFloatShowsRealDrift` — `19.99 + 19.99` → real observed text `"39.98"` (exact, no visible drift in this case).
- `sumOfManyMoreSmallValuesShowsSqliteSummationIsMoreStableThanNaive` — real, honest, **non-obvious finding**: summing `0.1` one thousand times produced exactly `"100.0"`, not the drifted value a naive left-to-right IEEE-754 accumulator would show. SQLite's real `SUM()` implementation is evidently more numerically careful than naive accumulation (a compensated/Kahan-style summation is plausible, though not confirmed from behavior alone) — recorded as real evidence, not the drift originally predicted before this was actually run.
- `classicZeroPointOnePlusZeroPointTwoBinaryFloatArtifactRealCheck` — the single most famous IEEE-754 non-exactness example (`0.1 + 0.2` → `0.30000000000000004` in naive binary float) produced exactly `"0.3"` under SQLite's real `SUM()`.

## The decision: Option A — exact Kotlin-side streaming aggregation, never SQL `SUM()`/`AVG()` on money

**Disqualifying reason is the confirmed type coercion itself, not visible drift in the tested samples.** Every representative case this milestone actually ran happened to produce a clean result — but `SUM()`/`AVG()` unconditionally hand back a `REAL` (confirmed via `typeof()`), which means the TEXT column's own exactness *guarantee* is lost the moment it passes through SQL aggregation, regardless of how well SQLite's particular summation algorithm performs on any given input set. A future, untested combination of magnitudes, scales, or heavy subtraction (e.g. many small refunds netted against large sales) is not proven safe merely because these representative cases were — and there is no way to prove a floating-point aggregate is exact for *all* possible inputs, only that it wasn't observed to drift for the ones tested. Trusting it as the *authoritative* reporting number would silently reintroduce exactly the risk `Money`'s own `BigDecimal`-backed exact arithmetic (`money-decimal-decision.md`, M3) was built to eliminate everywhere else in this codebase.

**Chosen strategy**: reporting aggregation reads rows via an indexed SQLDelight query (real index usage verified the same way `product-inventory-query-plan-report.md` already established the discipline for), parses each row's TEXT money/quantity through `Money.parse()`/`Quantity.zeroOrMore()` (the same parsing this codebase already trusts everywhere), and accumulates via `Money.plus`/`minus` — exact `BigDecimal` arithmetic, the identical mechanism `Cart`'s own `subtotal`/`total` properties already use (M3, unchanged). SQL is used only to *narrow the row set* (date range, company/branch scope, status filter) via `WHERE`/indexed predicates — never to compute the final authoritative sum.

## What this means concretely for M5.6's reporting queries

- `SELECT ... FROM sales WHERE company_id=? AND created_at >= ? AND created_at < ?` (real index-backed row projection) → Kotlin loop → `Money` accumulation. **Never** `SELECT SUM(total) FROM sales WHERE ...`.
- The one narrow exception already established and reused, not reopened here: `low-stock-definition.md`'s `CAST(... AS REAL)` in `selectLowStockProducts` — that is a **threshold comparison** (`<=` reorder level), not a stored/returned authoritative financial total, and remains out of this decision's scope (unchanged, M5.5).
- If chart rendering later needs `Float`/`Double` coordinates (a real future UI concern, not this milestone's), those are computed **only from the already-exact `Money` total**, at the final display boundary, and never read back as a business value — matching the governing spec's own explicit instruction.

## Real, executed evidence summary

`SqliteTextMoneyAggregationTest.kt`: 6/6 passing, part of the full 193/193 suite.
