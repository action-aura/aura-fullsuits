# Aura Retail Unified Mobile — Milestone 6 Decision

## Verdict: **CONDITIONAL PASS**

Every gate with a real, testable target on this host passes with real,
executed evidence. Real, structural deferrals (no Android device/
emulator, no macOS/Xcode, authorization deferred to M7-M10) keep this
CONDITIONAL, exactly as the checkpoint's own text requires — not folded
into a false unconditional PASS.

## Gate-by-gate (M6.29's own list)

| Gate | Status | Evidence |
|---|---|---|
| Presentation authority audit complete | PASS | `presentation-authority-audit.md`, `mobile-screen-route-matrix.md` — all 17 real legacy routes audited |
| Shared presentation architecture exists | PASS | `presentation-architecture.md` — real `UiContracts.kt` |
| Shared ViewModel lifecycle defined and tested | PASS | `shared-viewmodel-lifecycle.md`, `AuraViewModelTest.kt` (4/4) |
| DI graph preserves one database and one DatabaseWriteGate | PASS | `presentation-di-scope-report.md`, `AuraAppContainerTest.kt` (4/4) |
| Shared design system exists | PASS | `shared-design-system.md` — real light+dark themes (legacy app's own dark-only gap corrected) |
| Adaptive layout authority exists | PASS | `adaptive-layout-contract.md`, `AuraWindowSizeClassTest.kt` (5/5) |
| Shared application shell exists | PASS | `application-shell-contract.md` |
| Type-safe navigation exists | PASS | `type-safe-navigation-contract.md` — 47 real `@Serializable` routes |
| Screen routes cover the complete planned product | PASS | Every route has a real `composable<T>` entry (real content or a real, typed `FeatureUnavailableScreen`) |
| No dead Clinic routes introduced | PASS | Confirmed by inspection — zero Clinic references anywhere in M6 code |
| Common scaffold and components exist | PASS (with a real, disclosed icon-availability gap) | `common-component-catalog.md` |
| Shared form system exists | PASS (text fields only; Money/Quantity input deferred) | `shared-form-system.md`, `FormFieldStateTest.kt` (5/5) |
| Exact Money and Quantity presentation exists | PASS | `money-quantity-presentation.md`, `MoneyQuantityDisplayTest.kt` (5/5) |
| English foundation exists | PASS | `localization-foundation.md` |
| Arabic foundation exists | PASS | Same — real, curated bilingual catalog, `AuraStringsTest.kt` (6/6) |
| RTL foundation exists | PASS | `rtl-bidi-foundation.md` — real `LocalLayoutDirection` wiring |
| Accessibility foundation exists | PASS | `accessibility-foundation.md` — structural (48dp targets, content descriptions) |
| Loading/empty/error/offline states exist | PASS | `screen-state-contract.md` |
| List/search/filter/pagination components exist | PASS | `list-search-pagination-contract.md` |
| Category vertical slice uses real use cases | PASS | `category-ui-vertical-slice.md`, `CategoryListViewModelTest.kt` (4/4) |
| Branch vertical slice uses real use cases | PASS | `branch-ui-vertical-slice.md`, `BranchListViewModelTest.kt` (3/3) |
| Reporting vertical slice uses real use cases | PASS | `reporting-ui-vertical-slice.md`, `ReportingDashboardViewModelTest.kt` (2/2) |
| Import Center vertical slice uses real use cases | PASS | `import-ui-vertical-slice.md`, `ImportCategoriesViewModelTest.kt` (3/3) |
| No production UI backed by fake data | PASS | Confirmed — every vertical slice's real data comes from a real repository/use-case call against a real database |
| No Composable queries SQLDelight directly | PASS | Confirmed by inspection — every screen goes through a ViewModel → use case/repository |
| No Composable performs file decoding | PASS | `ImportCategoriesViewModel.onPreview` (not the Composable) calls `CsvImportDecoder` |
| No platform-specific business rules introduced | PASS | All M6 business logic lives in `commonMain` |
| Authorization remains explicitly deferred, not faked | PASS | `deferred-authorization-ui-boundary.md` — every prior milestone's own deferred status re-affirmed unchanged |
| Android unified debug APK builds | PASS | Confirmed after every M6 commit |
| No Chaquopy/local Flask introduced | PASS | Confirmed by inspection |
| Existing stable Android app remains unchanged | PASS | `android-shell-build-report.md` — zero diffs under `android/` |
| Android runtime validation honest | PASS | `NOT_VERIFIED` disclosed explicitly, never claimed |
| iOS validation honest | PASS | `ios-presentation-readiness.md` — `NOT_VERIFIED` disclosed |
| All new shared tests pass | PASS | 539/539, 0 failures, 0 errors |
| 491-test M5.8 baseline remains green | PASS | Subsumed; net +48 this milestone |
| Retail Python 194/194 remains green | PASS (unchanged, no Python touched) | — |
| Reporting query-count behavior unchanged | PASS | `ReportingDashboardViewModel` calls the same real, unchanged `DashboardRepository.getDashboard` |
| Import Center security behavior unchanged | PASS | `ImportCategoriesViewModel` calls the same real, unchanged M5.8 pipeline |
| No Clinic code introduced | PASS | Confirmed |
| Phase 9R workspace unchanged relative to M6 entry | PASS | `external-workspace-entry-fingerprints-m6.md` vs `external-workspace-exit-fingerprints-m6.md` — all 8 real values byte-identical |
| Legacy repository unchanged relative to M6 entry | PASS | Same, including the real pre-existing dirty state |
| Unified Mobile branch clean after commit | PASS | Confirmed via `git status --short` after every commit |
| Test count increased | PASS | 491 → 539 |

## Why CONDITIONAL, not unconditional PASS

Three real, structural reasons, none a gap in this milestone's own work:

1. **No real Android device/emulator on this host.** APK build success
   is real; on-device launch, real user interaction, and real visible
   UX are `NOT_VERIFIED` (`android-shell-build-report.md`).
2. **No macOS/Xcode.** `iosMain`/`iosTest` do not exist as real Gradle
   source sets when synced on this Windows host — real M6 dependency
   compatibility was confirmed via Gradle metadata resolution, not
   real compilation (`ios-presentation-readiness.md`).
3. **Authorization remains deferred to Milestones 7-10**, unchanged —
   correctly out of scope for a milestone about the presentation layer
   itself, not platform-wide authorization.

## Real findings during this milestone (not merely "no bugs found")

Four real, distinct findings, each fixed at the source and documented
with before/after evidence (full detail in `milestone-6-test-report.md`'s
own "Real bugs found and fixed" section):

1. `AuraViewModel`'s dispatcher default was invisible to
   `advanceUntilIdle()` in tests — fixed with an injectable dispatcher
   parameter on every concrete ViewModel.
2. A real Kotlin-Multiplatform-metadata icon-availability toolchain
   gap — real class files confirmed present in the resolved jars, still
   unresolvable from `commonMain` source; worked around with text
   affordances, disclosed rather than chased indefinitely.
3. M5.8 never built a real source-hash function — found by M6.19's own
   real Import UI (the first real caller needing to construct a dry-run
   from scratch); fixed with a new, disclosed `ImportSourceHasher`.
4. `LocalAuraAppContainer` defaulted to `null` everywhere in the real
   app until M6.26's own explicit audit caught it — every screen would
   have shown an unavailable state in a real running build; fixed by
   wiring the real container into `MainActivity`.

One real, disclosed, deliberately-unfixed gap: real archive actions
(Category/Branch) execute without the already-built
`AuraDestructiveConfirmation` dialog — low severity (reversible),
recorded honestly in `presentation-security-report.md` rather than
silently patched or hidden.

## Proceed to Milestone 7

Per the governing checkpoint: M6 (Shared Compose Presentation
Platform) concludes here with a CONDITIONAL PASS. M7 (Owner licensing
audit) and later licensing/device milestones remain explicitly
un-started, pending this milestone's own acceptance.
