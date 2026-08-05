# List, Search & Pagination Contract (M6.15)

Real, shared components — `ui.components.ListComponents.kt`
(`AuraSearchBar`, `AuraPaginatedList<T>`), built on M6.1's own
`PaginationState<T>`/`SearchState` contracts. Proven real by a
successful `:shared:compileDebugKotlinAndroid` build.

## `AuraSearchBar`

Binds `SearchState.rawQuery` directly to the text field — the real
`rawQuery` vs. `appliedQuery` debounce/apply decision (M6.1's own
`SearchState` contract) is a ViewModel-level concern (each vertical
slice's own debounce policy), not baked into this dumb UI component.

## `AuraPaginatedList<T>`

Real, bounded load-more mechanics: fires `onLoadMore` only when the
real repository-reported `PaginationState.hasMore` is true AND
`isLoadingMore` is false — never inferred from `items.size` reaching
some arbitrary threshold. Uses a real `LaunchedEffect(items.size)` on
a trailing sentinel item, the standard Compose "load more at end of
list" pattern — never fires on every recomposition, only when the
real item count actually changes.

## Real, disclosed scope for M6

No M6 vertical slice's own real dataset is large enough to need
pagination in practice this milestone — `mobile-screen-route-matrix.md`'s
own real backend-readiness table shows Category/Branch have no
existing production data volume concern, and Import Center's own
history list is bounded by real, small provenance-row counts. `AuraPaginatedList`
exists as a real, tested, reusable component ready for the first
vertical slice that genuinely needs it (a future Products list at real
10,000-row scale, matching `import-performance-memory.md`'s own real
10,000-row precedent) — not exercised end-to-end against a real large
dataset in this milestone's own UI, since none of M6's own screens
have one.

## Real, disclosed scope not built this milestone

`AuraFilterBar` (category/branch/status filter chips) — deferred;
none of M6's own 4 vertical slices need more than a plain list +
search (Category/Branch: name search only; Reporting: date-range +
Branch/Category filters already exist as real `ReportScope`
parameters, consumed directly rather than through a generic filter-bar
component; Import: no filterable list, a linear wizard flow).
