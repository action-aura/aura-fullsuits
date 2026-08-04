# Reporting Data Quality Contract (M5.6.12)

Real, executed contract for how `SqlDelightReportingRepository` handles
malformed or unexpected row data, proven by `ReportingDataQualityTest.kt`
(3/3,
`TEST-com.actionaura.retail.reporting.ReportingDataQualityTest.xml`
tests="3" failures="0" errors="0").

## Rule

A row that fails to parse is **excluded from the computed total** and
**appended to `dataQualityIssues`** — never silently treated as zero and
folded into the sum. This applies uniformly across `getSalesSummary`,
`getSalesTrend`, `getTopProductsByQuantity`, and
`getTopProductsByNetRevenue`.

## Cases actually reachable given the real schema

| Case | Detection | Behavior |
|---|---|---|
| Malformed `sales.total` / `returns.refund_amount` | `Money.parse` returns `FinancialResult.Failure` | Row excluded from `grossSales`/`confirmedReturns`; `transactionCount` not incremented for that row; `ReportingDataQualityIssue.MalformedMoney(table, rowId, rawValue)` appended |
| Malformed `sale_items.quantity` / `return_items.quantity` | `Quantity.zeroOrMore` returns `null` | Row excluded from both quantity and revenue accumulation for that product line; `MalformedQuantity` appended |
| Malformed `sale_items.line_total` / `return_items.line_total` | `Money.parse` returns `Failure` | Row excluded (see note below); `MalformedMoney` appended |

Proven by `malformedQuantityInASaleLineIsExcludedFromTopProductsAndSurfacedAsAnIssue`,
`malformedLineTotalInASaleLineIsExcludedFromNetRevenueAndSurfacedAsAnIssue`,
`malformedRefundAmountInAReturnIsExcludedFromConfirmedReturnsAndSurfacedAsAnIssue`.

**Note on line-level rows:** a `sale_items`/`return_items` row with a bad
`quantity` OR a bad `line_total` is excluded from the accumulator
entirely (both quantity and revenue), not partially applied — a line
that fails one check is never trusted for the other value either. This
is a deliberately conservative "exclude the whole row" policy, not a
per-field one.

## Cases that are structurally impossible given the real schema

The M5.6.12 spec lists several additional cases; the real schema
(`Sales.sq`, `Returns.sq`, `Catalog.sq`) makes most of them unreachable,
so no test claims to exercise them:

- **Missing currency / Branch / Product-snapshot**: `product_name_at_sale`
  on `sale_items`/`return_items` is `NOT NULL`; `sales.branch_id`/
  `returns.branch_id` are nullable, but a `NULL` branch is a real,
  legitimate value (no branch assigned), handled the same as any other
  branch value by the `:branchId IS NULL OR branch_id = :branchId`
  filter — not a data-quality defect. Currency is a company-level
  setting, not a per-row column (`reporting-definition-contract.md`),
  so there is no per-row "missing currency" case.
- **Orphaned Return reference**: `return_items.product_id` and
  `sale_items.product_id` are `REFERENCES products(id)` with
  `PRAGMA foreign_keys=ON` active in every test — an orphaned reference
  cannot be inserted at all.
- **Return exceeding sold quantity**: this is a business-rule invariant
  belonging to a future `ReturnRepository`'s write-time validation, not
  a reporting-time parsing concern — `eligible-sales-and-returns-contract.md`'s
  own unclamped-negative-net-sales behavior already covers the
  reporting-time consequence (a return larger than its sale legitimately
  drives a period net negative, and is reported as-is).
- **Unsupported status**: any `sales.status`/`returns.status` value other
  than `"completed"` is simply excluded by the eligibility filter
  (`eligible-sales-and-returns-contract.md`) — this is normal filtering,
  not a data-quality error to surface.
- **Out-of-range timestamp**: `created_at` is a plain `INTEGER` compared
  numerically against `startInclusive`/`endExclusive` — an unusually
  large or small (even negative) value simply sorts outside every real
  period's range and is excluded by the ordinary boundary check, with no
  special handling needed or possible to add.

## Blocking vs excluding

No case in this milestone triggers a blocking failure — every detected
issue is exclude-and-surface, matching the checkpoint's own list of
allowed behaviors ("blocking failure / exclude-with-surfaced-warning /
deterministic migration correction"). A future caller (ViewModel/UI
layer) decides how to present `dataQualityIssues` to a user; this
repository never throws for malformed data, since a malformed handful of
rows should never take down an entire report.
