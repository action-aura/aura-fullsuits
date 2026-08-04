# Sales Trend Authority Contract (M5.6.5)

Real, executed contract for `SqlDelightReportingRepository.getSalesTrend`,
proven by `SalesTrendAuthorityTest.kt` (6/6 real tests,
`TEST-com.actionaura.retail.reporting.SalesTrendAuthorityTest.xml`
tests="6" failures="0" errors="0").

## Shape

```kotlin
suspend fun getSalesTrend(scope: ReportScope, buckets: List<Pair<ReportPeriod, String>>): SalesTrendResult
```

The caller supplies the buckets (from `ReportPeriodFactory.dailyBuckets`/
`weeklyBuckets`/`monthlyBuckets`) and a display label key per bucket —
this repository never computes bucket boundaries itself
(`reporting-period-timezone-contract.md` already owns all of that math).
Each output `SalesTrendBucket` reuses the exact same
`CurrencySalesSummary` computation as `getSalesSummary`
(`eligible-sales-and-returns-contract.md`), so trend numbers and summary
numbers can never drift apart from having two separate formulas.

## Deterministic zero buckets

A calendar day/week/month with no eligible sales or returns still
produces a real bucket with `grossSales = Money.ZERO`,
`confirmedReturns = Money.ZERO`, `transactionCount = 0` — never an
absent/skipped bucket. Proven by
`emptyDaysProduceDeterministicZeroBucketsNotMissingBuckets`: requesting 3
consecutive days with sales only on day 1 and day 3 returns exactly 3
buckets, with day 2 a real zero bucket.

## No duplicate boundary inclusion

Half-open `[startInclusive, endExclusive)` per bucket
(`reporting-period-timezone-contract.md`) means a sale at the exact
instant a boundary falls on belongs to exactly one bucket, never both
and never neither. Proven by
`aSaleOnTheExactEndExclusiveBoundaryBelongsToTheNextBucketNeverBoth`: a
sale at exactly midnight (`day-1`'s `endExclusive`, `day-2`'s
`startInclusive`) lands in `day-2` only.

## Stable chronological ordering

`SalesTrendResult.buckets` preserves the exact order of the
caller-supplied `buckets` list — no re-sorting, no re-derivation.
`ReportPeriodFactory.dailyBuckets`/`weeklyBuckets`/`monthlyBuckets`
already emit chronological order, so callers using those factories get a
chronological result by construction. Proven by
`bucketsAreReturnedInStableChronologicalOrderMatchingTheCallersInput`.

## Scope captured once, no current-Branch mutation mid-execution

`ReportScope` is a plain immutable parameter passed once into
`getSalesTrend`; there is no code path that re-reads a mutable
"current branch" or "current currency" mid-computation — currency is
resolved once before the gate lock is acquired (see
`SqlDelightReportingRepository`'s own KDoc on the Mutex-reentrancy
hazard), and every bucket in one call shares that single resolved
currency. Proven by
`scopeIsCapturedOnceAtInvocationAndUnaffectedByCurrencySettingChangesDuringExecution`.

## Archived Branch history remains reportable

Deactivating a Branch (`BranchRepository.setActive(..., false)`) does
not remove or hide its historical sales from the trend — `sales.branch_id`
is a plain foreign key with no active/inactive filter in
`selectSalesForPeriod`. Proven by
`archivedBranchHistoricalSalesRemainReportableInTheTrend`.

## Cross-business Branch scoping cannot leak

Every reporting query filters `company_id = :companyId AND ... AND
(:branchId IS NULL OR branch_id = :branchId)` — a `branchId` that
belongs to a *different* company's rows can never match because
`company_id` is checked independently, so passing another company's
`branchId` structurally returns zero rows rather than that company's
data. This is a **structural** safety property already present in
`Reporting.sq`'s `WHERE` clauses, not a new access-control check —
real *authorization* (which businesses/branches a caller is allowed to
request in the first place) remains `DEFERRED_TO_MILESTONES_7_TO_10`
(`reporting-authorization-integration-boundary.md`). Proven by
`requestingAnotherCompanysBranchIdNeverLeaksThatCompanysSales`.

## No localized prose in the domain layer

`bucketLabelKey: String` is a stable, caller-supplied key (e.g.
`"day-0"`, or a real ISO date string a future UI layer would choose),
never a localized display string produced by this repository —
localization is a UI-layer concern outside M5.6's scope.
