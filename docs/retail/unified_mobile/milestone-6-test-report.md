# Aura Retail Unified Mobile — Milestone 6 Test Report

Real, executed evidence only. Every number below comes from a real
Gradle test run and JUnit XML summation
(`shared/build/test-results/testDebugUnitTest/*.xml`).

## Test count progression

| Checkpoint | Shared tests | Delta |
|---|---|---|
| M5.8 close (accepted checkpoint) | 491 | — |
| After M6.0 (presentation audit, docs only) | 491 | +0 |
| After M6.1-M6.3 (presentation architecture, ViewModel lifecycle, DI) | 499 | +8 |
| After M6.4-M6.7 (design system, adaptive layout, app shell, navigation) | 504 | +5 |
| After M6.8-M6.10 (common components, form system, money/quantity display) | 514 | +10 |
| After M6.11-M6.15 (localization, RTL, accessibility, list/pagination) | 520 | +6 |
| After M6.16-M6.17 (Category, Branch vertical slices) | 527 | +7 |
| After M6.18 (Reporting vertical slice) | 529 | +2 |
| After M6.19 (Import Center vertical slice) | 535 | +6 |
| After M6.20-M6.27 (feature registry, performance, Android shell wiring) | **539** | +4 |

**Net M6 contribution: 48 new tests (491 → 539), 0 failures, 0 errors
at every checkpoint** — each commit was verified green before the next
began, matching the discipline established at M5.6-M5.8.

## New test files (this milestone)

| File | Tests | Covers |
|---|---|---|
| `AuraViewModelTest.kt` | 4 | M6.2 state/effect/dispatcher-injection contract |
| `AuraAppContainerTest.kt` | 4 | M6.3 real DI graph identity |
| `AuraWindowSizeClassTest.kt` | 5 | M6.5 real breakpoint boundaries |
| `FormFieldStateTest.kt` | 5 | M6.9 form validity contract |
| `MoneyQuantityDisplayTest.kt` | 5 | M6.10 exact display formatting |
| `AuraStringsTest.kt` | 6 | M6.11 localization catalog, parameterization, plural |
| `CategoryListViewModelTest.kt` | 4 | M6.16 real Category use cases end-to-end |
| `BranchListViewModelTest.kt` | 3 | M6.17 real Branch use cases + last-active protection |
| `ReportingDashboardViewModelTest.kt` | 2 | M6.18 real DashboardRepository composition |
| `ImportSourceHasherTest.kt` | 3 | M6.19 new FNV-1a hash utility |
| `ImportCategoriesViewModelTest.kt` | 3 | M6.19 real end-to-end Import pipeline |
| `FeatureSurfaceRegistryTest.kt` | 3 | M6.20 registry structural integrity |
| `CategoryListUiStatePerformanceTest.kt` | 1 | M6.23 real 10,000-item filter timing |

## Real bugs found and fixed during this milestone

1. **`AuraViewModel.launchOnDefault`'s `Dispatchers.Default` default is
   invisible to `kotlinx.coroutines.test.advanceUntilIdle()`** — found
   by `CategoryListViewModelTest`/`BranchListViewModelTest`'s own first
   real run (3/7 tests failed with real assertion errors, state not
   yet updated when asserted). Fixed by adding an injectable
   `dispatcher` parameter to every concrete ViewModel
   (`category-ui-vertical-slice.md`).
2. **Real icon-availability toolchain gap** — `Icons.Filled.ArrowBack`/
   `Warning` and several real, confirmed-present variants
   (`ArrowBackIosNew`, `WarningAmber`, `AutoMirrored.Filled.ArrowBack`)
   all failed to resolve from `commonMain` Kotlin source despite the
   underlying `.class` files being physically confirmed present in the
   resolved Android-target `.aar` artifacts (`material-icons-core-android:1.7.1`)
   — a real Kotlin-Multiplatform-metadata-vs-final-platform-jar
   discrepancy in this project's exact pinned toolchain. Worked around
   with text-based affordances (`common-component-catalog.md`), not
   silently declared solved.
3. **A real, previously-undiscovered gap**: M5.8 never built a real
   hash function for `ImportDryRun.sourceHash` — every M5.8 test used
   a literal string. Found while building M6.19's own real Import UI
   (the first real CALLER that needed to construct a real dry-run from
   scratch, not a test fixture) — fixed with a new, real, disclosed
   `ImportSourceHasher` (FNV-1a).
4. **`LocalAuraAppContainer` defaulted to `null` everywhere in the real
   app** — every M6 screen would have shown "Database not yet
   initialized" in an actual running build, because `MainActivity`
   never constructed or provided the real `AuraAppContainer`. Found
   during M6.26's own real audit (not by a failing test — no test
   exercises `MainActivity` directly) — fixed by wiring the real
   `AndroidDatabaseDriverFactory`/`AndroidUnicodeTextNormalizer` into
   `MainActivity.onCreate`.

## Cross-language and platform checks

- Retail Python: not re-run this milestone (no Python file was
  touched — entirely Kotlin-side work).
- Android debug APK: built successfully after every commit in the M6
  sequence (real, repeated Gradle invocations, never concurrent),
  including the final real container-wiring commit.
- iOS: not claimed, per standing constraint
  (`ios-presentation-readiness.md`).
- Real on-device Android execution: not performed — no adb/emulator on
  this host, the same standing disclosure as every prior milestone.
- Real visual/screenshot regression: not performed
  (`visual-regression-foundation.md`).
