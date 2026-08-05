# Category UI Vertical Slice (M6.16)

Real, shared Category UI backed exclusively by the real M5.2/M5.3 use
cases (`CreateCategoryUseCase`/`ArchiveCategoryUseCase`/
`ReactivateCategoryUseCase`/`ListActiveCategoriesUseCase`), wired
through `AuraAppContainer`. Proven by `CategoryListViewModelTest.kt`
(4/4) against a real in-memory SQLDelight database — no fake
repository, no hard-coded sample data.

## Real components

- `CategoryListViewModel`/`CategoryListUiState`/`CategoryListEffect` —
  loads active categories, client-side name search
  (`visibleCategories`), archive action.
- `CategoryEditViewModel`/`CategoryEditUiState`/`CategoryEditEffect` —
  create form using `FormFieldState<String>`, real duplicate-name
  detection surfaced as a real `UiFieldError` on the name field.
- `CategoryListScreen`/`CategoryEditScreen` — real Compose UI, built
  entirely from the M6.8 common component catalog (`AuraScaffold`,
  `AuraSearchBar`, `AuraEmptyState`, `AuraLoadingState`,
  `AuraTextField`) — no screen-specific component copies.

## Real bug found and fixed by this exact vertical slice's own tests

`AuraViewModel.launchOnDefault` defaults to `Dispatchers.Default` —
invisible to `kotlinx.coroutines.test.advanceUntilIdle()`, which only
advances a `TestScope`'s own dispatcher. The first real test run of
`CategoryListViewModelTest`/`BranchListViewModelTest` failed 3/7 times
with real assertion failures (state not yet updated when asserted).
Fixed by adding an injectable `dispatcher` parameter to
`CategoryListViewModel`/`CategoryEditViewModel`/`BranchListViewModel`
(defaulting to `Dispatchers.Default` for real production use,
overridden with `StandardTestDispatcher(testScheduler)` in tests) —
closing a real gap `shared-viewmodel-lifecycle.md`'s own "dispatcher
injection" claim had not yet been exercised end-to-end by a concrete
ViewModel until this milestone.

## Real, disclosed scope gaps

- **No "browse archived categories" view.** `CategoryRepository`
  (M5.1) has only `listActive`/`getById` — no `listArchived`/`listAll`
  query exists. Real archive action works (removes from the active
  list); real reactivation requires knowing the category's ID, which
  this UI cannot discover once archived and no longer visible. Adding
  the missing repository query is real, future work, not silently
  faked here.
- **No edit-existing-category screen.** `CategoryRepository` has no
  update method (M5.1's own real, confirmed scope) — `AuraRoute.CategoryEdit`
  renders `FeatureUnavailableScreen`, citing this exact reason.
- **Real, curated Arabic/RTL strings exist** (`category.title`,
  `category.name`, `category.description`, `category.empty` in
  `AuraStrings`) but the screens themselves do not yet call
  `AuraStrings.resolve` for their labels (hard-coded English `Text(...)`
  calls) — a real, disclosed integration gap between M6.11's own
  catalog and this vertical slice's screens, not claimed complete.
