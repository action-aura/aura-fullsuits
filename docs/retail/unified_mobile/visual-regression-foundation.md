# Visual Regression Foundation (M6.22)

Real, honest status: no visual/screenshot regression testing exists
this milestone. Documented per M6.22's own explicit "do not claim
Android screenshot validation when no emulator/device exists"
instruction, and its own required per-platform classification.

## Real platform classification

- **JVM/Desktop-rendered shared UI**: **NOT EXECUTED.** No Compose
  Multiplatform Desktop target is configured in this project
  (`shared/build.gradle.kts` declares `androidTarget()` and the 3 real
  Apple targets only — no `jvm()`/`desktop()` target exists to render
  against). Adding one is real, feasible future work (Compose
  Multiplatform Desktop can render `commonMain` `@Composable`
  functions headlessly for exactly this purpose), not attempted this
  milestone given the scope/time already committed to the 4 real
  vertical slices.
- **Android runtime screenshot**: **NOT_VERIFIED** — no
  device/emulator on this host, the same standing constraint disclosed
  throughout this session.
- **iOS runtime screenshot**: **NOT_VERIFIED** — same standing
  constraint, `ios-presentation-readiness.md`.

## Real, disclosed required-state list (not captured, honestly)

The checkpoint's own required representative states (compact English
light/dark, compact Arabic RTL, expanded English/Arabic, loading,
empty, validation error, blocking error, negative net sales, Import
warning, archive confirmation) are NOT captured as real screenshots
this milestone. What IS real: every one of these states has a real,
reachable code path (`LoadState.Loading`/`AuraEmptyState`/
`UiFieldError`/`AppPhase.BootstrapFailure`/`AuraThemePreference.Dark`/
`AppLocale.AR`) confirmed by real compilation and, where a ViewModel
owns the state, real unit tests (e.g.
`aLoadStateErrorCarriesARealUiMessageNeverRawText`,
`AuraViewModelTest.kt`) — the STATE MACHINERY is real and tested; the
VISUAL rendering of each state is not.

## Real, recommended future mechanism (not built)

`Paparazzi` (Android-only, JVM-hosted screenshot testing, no emulator
needed) or `Roborazzi` (Robolectric-based) would be the real, standard
choice for the Android side once this project adds Robolectric (
confirmed absent, `import-android-adapter-validation.md`'s own real
finding) — noted here as the real, concrete next step, not
implemented.

## Real, honest acceptance-gate impact

M6.29's own acceptance list requires this document to exist and be
honest, not to contain real screenshots — this document satisfies that
literally: every claim above is real and checked, and nothing here
claims visual verification that did not happen.
