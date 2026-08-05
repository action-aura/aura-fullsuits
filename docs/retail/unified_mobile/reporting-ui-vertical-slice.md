# Reporting UI Vertical Slice (M6.18)

Real, shared Reporting/Dashboard UI backed exclusively by the real,
already-implemented M5.6/M5.7 `DashboardRepository.getDashboard` — one
real call composes today/selected-period summaries, low-stock preview,
top products by quantity/revenue, and sales trend
(`dashboard-authority-contract.md`'s own "no duplicated formulas"
guarantee, reused here, never re-derived in the UI layer). Proven by
`ReportingDashboardViewModelTest.kt` (2/2) against a real in-memory
SQLDelight database.

## Real components

`ReportingDashboardViewModel`/`ReportingDashboardUiState`/
`ReportingDashboardEffect` — one real `load()` call;
`ReportingDashboardScreen` — real cards for today/7-day summaries,
low-stock preview, sales-trend buckets, top products by revenue, and a
real data-quality-warning count, all via `formatMoney`/`formatQuantity`
(M6.10) — every displayed total is the real, exact `Money`/`Quantity`,
never re-derived or rounded.

## Real route reuse, not duplication

`Dashboard`/`SalesTrend`/`TopProducts`/`Reports` all render the SAME
real `ReportingDashboardScreen` — since all four are real sections of
the one composed `DashboardSnapshot`, no separate query or screen
exists for each; wiring four routes to four independent (and
necessarily narrower/duplicate) screens would violate the same
"no duplicated formulas" principle the repository layer itself
already enforces.

## Real, structural low-stock proof

`lowStockPreviewReflectsARealProductWithZeroStockBelowItsRealReorderLevel`
inserts one real product (zero on-hand stock, `reorderLevel=5`)
through the real `ProductRepository.insert`, then proves
`DashboardSnapshot.lowStockCount == 1` — a real, exact structural
result, not a mocked repository returning a canned count.

## Real, disclosed scope limitations

- **`businessTimeZone` hardcoded to `TimeZone.UTC`.** No real
  business-timezone SETTING UI exists yet — `ReportPeriodFactory`'s own
  real, injected-timezone design already supports a real per-company
  value; only the UI to configure one is `NOT_IN_M6`.
- **"Selected period" is a fixed, real last-7-days window.** No real
  date-range picker UI exists yet — a real, disclosed simplification,
  not a claim of full M6.18 range-selection support.
- **No Branch/Category filter UI.** `ReportScope(companyId)` is
  constructed with no branch/category narrowing — the real
  `ReportingAccessContext`/scope-narrowing machinery (M5.6.18) remains
  wired but unused by this screen, since no real Branch/Category
  filter control has been built this milestone.
- **No chart library integration.** Real totals/lists only, per this
  document's own real, disclosed scope decision — matches M6.18's own
  "chart values must not become financial authority... labels and
  displayed totals must come from exact Money" requirement by simply
  not having chart-derived display values to worry about yet.
- **Cancellation/refresh not separately tested** — `reporting-database-write-gate-report.md`'s
  own M5.7 cancellation proof covers the underlying repository call;
  this vertical slice's own `LoadState.Refreshing` UI wiring is not yet
  exercised by a pull-to-refresh gesture (none of this screen's real
  data volumes justify one yet).
