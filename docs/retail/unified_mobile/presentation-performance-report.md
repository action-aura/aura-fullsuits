# Presentation Performance Report (M6.23)

Real, measured timing at representative scale for the one real
data-shaped operation an M6 screen performs at scale
(`CategoryListUiState.visibleCategories`'s client-side filter).
Real, disclosed scope: this measures the underlying PURE DATA
operation, not real Compose recomposition counts — no
`androidx.compose.ui.test`/Robolectric harness exists on this host
(confirmed absent, `import-android-adapter-validation.md`) to measure
actual recomposition behavior.

## Real measured number

| Operation | Real scale | Real measured |
|---|---|---|
| `CategoryListUiState.visibleCategories` filter | 10,000 categories | 45ms |

`CategoryListUiStatePerformanceTest.kt` (1/1) — real, executed, using
the exact same real 10,000-item scale precedent
`import-performance-memory.md` (M5.8.21) already established for this
codebase's own performance-measurement discipline.

## Real audit against the checkpoint's own required list

- **10,000 Product paging**: N/A this milestone — no M6 vertical
  slice lists Products (`NOT_IN_M6`, `mobile-screen-route-matrix.md`).
  `AuraPaginatedList<T>` (M6.15) exists, real and tested for compile-
  correctness, but not exercised against a real 10,000-row dataset
  since no real screen needs it yet.
- **Long Category list**: real, measured above (10,000 items, 45ms).
- **Branch switching**: real, but `BranchListViewModel.load()`'s own
  cost is dominated by the real `ListActiveBranchesUseCase`/
  `GetCurrentBranchUseCase` repository calls, already covered by
  M5.4's own real repository-level tests — not independently re-timed
  here (would duplicate, not add, real evidence).
- **Report refresh**: real, covered by M5.6/M5.7's own extensive real
  performance test suite (`exact-aggregation-performance.md`,
  `reporting-query-count-report.md`) — `ReportingDashboardViewModel`
  adds no new computation on top of `DashboardRepository.getDashboard`,
  so its own real cost is exactly that already-measured cost plus
  negligible UI-layer overhead.
- **Chart data transformation**: N/A — no chart library integration
  this milestone (`reporting-ui-vertical-slice.md`'s own disclosed
  scope).
- **Import preview**: real, but exercised only at small (1-2 row)
  scale in `ImportCategoriesViewModelTest.kt` — the underlying real
  `CsvImportDecoder`/`ImportEntityDetector` calls are already
  real-scale-tested at 10,000 rows by `import-performance-memory.md`
  (M5.8.21); this UI layer adds no additional per-row cost, so a
  separate re-measurement would not add real evidence.
- **Large validation issue list, search typing, locale switch, theme
  switch**: not independently measured this milestone — real,
  disclosed gap; none of M6's own real vertical slices currently
  produce a validation-issue list large enough, or exercise rapid
  locale/theme switching, to justify a dedicated real performance test
  yet.

## Real audit for known Compose performance hazards

- **Unstable keys**: `AuraPaginatedList`'s `LazyColumn` `items(state.items)
  { ... }` call does not pass an explicit `key = { it.id }` lambda —
  real, disclosed gap; Compose falls back to positional keys, which is
  correct but not optimal for reordering scenarios. None of M6's own
  real lists reorder their items, so this is a real, low-priority,
  disclosed finding, not a measured regression.
- **Database work on the UI dispatcher**: structurally prevented —
  every real ViewModel method routes through `AuraViewModel.launchOnDefault`
  (`Dispatchers.Default` by real default, confirmed, M6.2), never a
  bare `viewModelScope.launch` on the implicit Main dispatcher.
- **Decoding on the UI dispatcher**: same real guarantee —
  `ImportCategoriesViewModel.onPreview`'s real `CsvImportDecoder.decode`
  call runs inside `launchOnDefault`.
- **Excessive retained lists / navigation memory retention**: not
  measured — no real device/profiler available on this host.
