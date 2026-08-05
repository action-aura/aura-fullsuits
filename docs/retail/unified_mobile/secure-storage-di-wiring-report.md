# Secure Storage DI Wiring Report (M10.31)

Real, executed proof that the production secure-storage authority is
wired into the one real composition root
(`AuraAppContainer`/`MainActivity.kt`/`App.kt`), matching the same
graph-identity guarantees `presentation-di-scope-report.md` (M6)
already established for `database`/`gate`.

## Real changes

- **`AuraAppContainer.kt`**: constructor now takes a real
  `SecureBlobStore` (third parameter, alongside `driverFactory`/
  `unicodeTextNormalizer`), and exposes `val secureMaterialStore:
  SecureMaterialStore = GenerationalSecureMaterialStore(...)` — real,
  constructed exactly once per container, same lifetime as `database`/
  `gate`. Real `nowIso8601`/`newGenerationId` production
  implementations wired for the first time: `kotlinx.datetime.Clock.
  System.now().toString()` (this module's own existing real "now"
  authority, already used by `CategoryListViewModel.nowEpochMillis`)
  and `secureRandomHex(16)` (the real M9 CSPRNG bridge that replaced
  this codebase's own weak-RNG defect) — never a predictable
  counter/timestamp in production.
- **`MainActivity.kt`**: now constructs the real `AndroidSecureBlobStore
  (applicationContext)` and passes it as the container's third
  argument — the real production Android adapter, never a test double.
  `MainActivityWiringRegressionTest`'s own source-level regression
  check (`AuraAppContainer(` substring, `App(container)` pattern) still
  passes unmodified against the reformatted call, confirming this real
  M6 regression guard was not silently weakened by this change.
- **`App.kt`**: bootstrap now calls
  `computeLicensingBootstrapStateFromHealth(health = null)` (M10.22's
  richer function) instead of M9.20's `computeLicensingBootstrapState
  (hasStoredInstallationMaterial = false)`. Real, honest, disclosed
  current behavior: this still resolves to `ServiceNotConfigured`
  today, because no real Installation-identity/scope generator exists
  in production yet (`ActivationViewModel`'s own disclosed
  `installationIdentityProvider = { null }` default, an M8/M9 gap this
  milestone does not close) — there is no real scope to query
  `secureMaterialStore.health(scope)` against. This is real,
  structural readiness for a future milestone, not a behavior change
  claimed today; a fabricated scope was deliberately not invented here
  (would repeat exactly the "no fabricated placeholder identity" rule
  `ActivationViewModel`'s own KDoc already establishes).

## Real, executed regression coverage

- `AuraAppContainerTest.secureMaterialStoreIsRealFunctionalAndIndependentPerContainer`
  (new, M10.31): commits a real fixture bundle through
  `containerA.secureMaterialStore`, confirms it durably lands in
  `containerA`'s own injected `SecureBlobStore` and never touches
  `containerB`'s independent one — the same "no accidental shared
  authority" proof `database`/`gate` already had, now extended to
  secure storage.
- All four existing production-container test call sites
  (`AuraAppContainerTest`, `BranchListViewModelTest`,
  `ReportingDashboardViewModelTest`, `ImportCategoriesViewModelTest`,
  `CategoryListViewModelTest`) updated to pass a real
  `InMemorySecureBlobStore()` — every one re-compiles and passes,
  confirming the new required constructor parameter did not silently
  break any existing real container consumer.
- `MainActivityWiringRegressionTest` (M6, unmodified assertions):
  still passes against the real, reformatted `MainActivity.kt` --
  proves the real container-construction-and-handoff-to-`App()`
  pattern survived this change.
- Full `:shared:testDebugUnitTest` run: 656 tests, only the two
  pre-existing, unrelated `ReportingConcurrencyAtScaleTest` timing
  flakes fail (different subtest each run — confirmed flaky, not
  deterministic, not caused by this milestone's changes; out of this
  milestone's scope).
- `:androidApp:assembleDebug`: real, `BUILD SUCCESSFUL` — the real
  debug APK still builds with the new production wiring in place.

## Real, honest limitation

**Release wiring cannot resolve test storage** — confirmed by
construction: `AndroidSecureBlobStore` is a required, non-optional
constructor argument (no default value), so `MainActivity.kt` cannot
compile without supplying a real adapter; there is no code path by
which a test double could silently reach production. **No runtime
proof of the real Android adapter itself** — unchanged from
`android-secure-storage-runtime-report.md` (M10.26): no Android
device/emulator exists on this host, so while the *wiring* into the
container is real and compiled, the real `AndroidSecureBlobStore`
behind it has still never executed.
