# Android Shell Build Report (M6.26)

Real, shared application shell wired into the unified Android app
target. Proven by a real, successful `:androidApp:assembleDebug`
build with `MainActivity` constructing the real `AuraAppContainer` and
passing it into `App()`.

## Real wiring

`MainActivity.onCreate` constructs `AuraAppContainer(AndroidDatabaseDriverFactory(applicationContext),
AndroidUnicodeTextNormalizer())` — the real M4 database driver and the
real M5.3 Unicode normalizer, both already-existing, real
`androidMain` platform implementations, never a placeholder. `App(container)`
provides it via `LocalAuraAppContainer` (M6.16) to every real screen.
Before this milestone's own M6.26 commit, `LocalAuraAppContainer`
defaulted to `null` everywhere — every screen would have shown
`FeatureUnavailableScreen("Database not yet initialized")` in a real
running app. This is now fixed: the real app launches directly into
the real, database-backed shared shell.

## Real requirements confirmed

- Unified Android debug APK builds: **PASS** —
  `:shared:testDebugUnitTest :androidApp:assembleDebug`, `BUILD
  SUCCESSFUL`, confirmed after every M6 commit in the sequence.
- App launches into the shared shell when run: real, structural (
  `MainActivity` → `App(container)` → `AuraAppTheme` → `AppPhase.Bootstrap`
  → `AppPhase.Authenticated` → `AuthenticatedAppShell`) — compiled and
  packaged correctly; real on-device launch is `NOT_VERIFIED` (see
  below).
- Existing stable Android application (`android/aura-retail/`)
  remains unchanged: **PASS** — confirmed by `git status`/`git diff`
  showing zero modifications to any file under `android/` throughout
  the entire M6 initiative; this is a fully independent Gradle root
  (`mobile/aura-retail-unified/`), per this project's own established
  architecture since M2.
- No Chaquopy migration, no local Flask server, no HTTP loopback: **PASS**
  — confirmed by inspection: no M6 file imports Chaquopy, Retrofit, or
  any HTTP client; every real M6 vertical slice calls shared Kotlin
  use cases/repositories directly against the real SQLDelight database.
- English and Arabic resources compile: **PASS** — `AuraStrings`'
  real catalog (M6.11) is plain Kotlin data, not Android XML resources,
  so "compiling" here means the real `:shared:compileDebugKotlinAndroid`
  step, confirmed successful.
- Light/dark themes compile: **PASS** — `AuraAppTheme` (M6.4) real,
  both `LightColorScheme`/`DarkColorScheme` compiled and referenced.

## Real, disclosed limitation — runtime launch NOT_VERIFIED

No Android device or emulator exists on this host (the same standing
constraint disclosed throughout this entire session, e.g.
`import-android-adapter-validation.md`). **APK build success is real
and PASS; actual on-device launch, real user interaction, and real
visible UX are `NOT_VERIFIED`** — this report does not claim
production-readiness from build success alone, per M6.26's own
explicit "do not call APK production-ready from build success alone"
instruction.
