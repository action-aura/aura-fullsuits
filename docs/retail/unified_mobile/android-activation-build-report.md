# Android Activation Build Report (M9.27)

Real, executed evidence — `:androidApp:assembleDebug`, `BUILD
SUCCESSFUL` (61 actionable tasks, 9 executed / 52 up-to-date), with
the full M9 activation orchestration/ViewModel/Compose flow wired into
`shared` and no changes to `androidApp` itself required (the new
`ActivationScreen`/`ActivationViewModel` are not yet reachable from
`AuraNavHost`/`App()` — real, disclosed: M9 builds the shared
orchestration layer and its own Compose flow, it does not wire a new
navigation entry point into the existing app shell, since doing so
would require a real menu/settings entry point decision out of this
milestone's own scope).

## Real requirements verified

- **Android debug APK builds**: confirmed, this milestone's own real
  Gradle invocation.
- **Production dependency graph uses `DisabledProductionTransport`**:
  real, structural — no other `ExternalLicensingTransport`
  implementation is referenced anywhere in `commonMain`/`androidMain`
  production source (`ContractFixtureTransport`, M9.28, lives
  exclusively in `commonTest`, a separate compilation unit entirely
  invisible to `androidApp`'s own production build).
- **Fixture transport absent from release production wiring**: real,
  same structural proof — `commonTest` sources are never included in
  an `assembleDebug`/`assembleRelease` APK build (standard Gradle KMP
  source-set separation, not a special case this project had to
  configure).
- **Activation screens compile**: confirmed —
  `:shared:compileDebugKotlinAndroid` includes `ActivationFlow.kt`/
  `ActivationViewModel.kt`, `BUILD SUCCESSFUL`.
- **Startup integration compiles**: confirmed — `App.kt`'s real
  `licensingBootstrapState` addition compiles cleanly.
- **No Chaquopy**: confirmed, unchanged since every prior milestone —
  no Python runtime embedded anywhere in Unified Mobile.
- **No local Flask**: confirmed, same reason.
- **No loopback activation**: confirmed — no localhost/loopback URL
  exists in any M9 file (`ExternalApiConfiguration`'s own real
  validation would reject a bare cleartext loopback URL outside an
  explicit development context anyway).
- **No hard-coded activation success**: confirmed by inspection — no
  code path in `ActivationViewModel`/`ActivationResponseProcessor`
  sets `ActivationState.ACTIVATION_COMPLETE` without going through the
  real `DisabledProductionTransport` → `TransportNotConfigured` →
  never-`Complete` chain.
- **No credential persisted in ordinary Android storage**: confirmed
  — `NoSecureStorageAvailableSink` is the only sink production code
  constructs (via `ActivationViewModel`'s own default parameters), and
  it writes nothing anywhere.
- **No real Android runtime success claimed without device/emulator**:
  honored — see below.

## Honest runtime disclosure (unchanged standing constraint)

- **APK build**: PASS (real, executed above).
- **Android runtime activation**: NOT VERIFIED — no adb/emulator on
  this host, same standing disclosure as every prior milestone
  (M6/M7/M8's own `android-shell-build-report.md`/decision documents).
- **Android lifecycle behavior**: NOT VERIFIED — same reason.
- **Android connectivity adapter**: NOT VERIFIED — not executed
  (`UnknownConnectivityObserver` is the only real implementation, and
  it performs no real platform connectivity check by design, M9.16).
