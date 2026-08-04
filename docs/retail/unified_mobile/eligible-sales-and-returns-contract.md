# Eligible Sales and Returns Contract (M5.6.3)

Real, executed contract for `SqlDelightReportingRepository`
(`shared/src/commonMain/kotlin/com/actionaura/retail/reporting/SqlDelightReportingRepository.kt`),
proven by `SqlDelightReportingRepositoryTest.kt` (9/9 real, seeded-row
tests, `shared/build/test-results/testDebugUnitTest/TEST-com.actionaura.retail.reporting.SqlDelightReportingRepositoryTest.xml`
tests="9" failures="0" errors="0").

## Eligible status

`ELIGIBLE_STATUS = "completed"`, applied to both `sales.status` and
`returns.status`. `reporting-authority-audit.md` §4 confirms the legacy
authority has no status filter at all today because every row is
unconditionally `'completed'` — the unified authority filters explicitly
so a real future non-`completed` status (`draft`, `cancelled`, `pending`,
etc.) is excluded the instant it exists, not silently included the way it
would be if this repository copied the legacy authority's own absence of
a filter.

Proven by `draftAndCancelledSalesAndReturnsAreNeverCounted`: a `draft`
sale, a `cancelled` sale, and a `pending` return are all seeded alongside
one `completed` sale; only the completed sale's 50.00 appears in
`grossSales`, confirmed returns stays `Money.ZERO`, and
`transactionCount` is exactly 1.

## Formulas

```
gross_sales      = sum of `total` over eligible sales in [startInclusive, endExclusive)
confirmed_returns = sum of `refund_amount` over eligible returns in [startInclusive, endExclusive)
net_sales        = gross_sales - confirmed_returns   (never clamped to zero)
```

All three accumulate via real Kotlin `Money.plus`/`Money.minus` (exact
`BigDecimal`), never SQL `SUM()`/`AVG()`
(`exact-report-aggregation-decision.md`).

Proven exact for two 19.99 sales plus one 10.00 return
(`grossReturnsAndNetAreExactAndTransactionCountIgnoresReturns`: gross
39.98, confirmed returns 10.00, net 29.98).

## Unclamped negative net sales

`net_sales` is reported as-is when returns exceed gross sales in a
period — this is a real, tested legacy invariant
(`test_revenue_becoming_negative_is_not_clamped`, cited in
`reporting-authority-audit.md` §1), not a new decision. Proven by
`negativeNetSalesFromLaterReturnsIsNeverClampedToZero`: a 10.00 sale and
a 59.97 return in the same period produce `netSales = Money.of(-49.97)`,
not zero.

## Transaction count (LEGACY_REQUIRED)

`transactionCount` = count of eligible (`completed`) `sales` rows only.
It is never reduced by returns, and there is no "fully reversed sale"
concept anywhere in this codebase to base a different definition on —
this matches the legacy authority's own `COUNT(*) FROM sales` exactly
(`reporting-authority-audit.md` §1). Proven in the same test above: two
sales and one return produce `transactionCount = 2`, not 1.

## Return-date policy (never retroactive)

A `Sale` contributes to `gross_sales` on its own `created_at` date
bucket. A `Return` contributes to `confirmed_returns` on its own
`created_at` (confirmation) date bucket — never on the date of the
original `Sale` it refers to. There is no code path that walks back to
`returns.sale_id` and re-attributes a return's value into the sale's own
period.

Proven by
`aSaleContributesOnItsOwnDateAndAReturnContributesOnItsOwnConfirmationDateNotTheOriginalSaleDate`:
a sale on 2026-01-15 and its return confirmed on 2026-02-03 — the
January 15 period shows the full 30.00 gross and zero confirmed returns;
the February 3 period shows zero gross and the full 30.00 confirmed
return.

## Branch scope

`ReportScope.branchId == null` means "all local branches, this company."
A non-null `branchId` narrows both the `selectSalesForPeriod` and
`selectReturnsForPeriod` queries via the SQLDelight
`:branchId IS NULL OR branch_id = :branchId` pattern. Proven by
`branchFilterScopesSalesAndReturnsToOneBranchOnly`.

## Deferred authorization

Nothing in this contract enforces which businesses/branches a caller is
*allowed* to request — `ReportScope.companyId`/`branchId` are trusted
inputs from the caller. Per the governing M5.5 checkpoint and M5.6.18,
real authorization binding is `DEFERRED_TO_MILESTONES_7_TO_10` — see
`reporting-authorization-integration-boundary.md` (M5.6.18) for the
integration boundary this repository is built to accept later.
