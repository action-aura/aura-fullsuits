# iOS Activation Orchestration Readiness (M9.26)

Real audit of every M9 dependency for Kotlin/Native (iOS) compatibility.

## Real dependency audit

| Dependency | Used by | iOS/Kotlin-Native support |
|---|---|---|
| `kotlinx.coroutines` (`Mutex`, `withLock`, `suspend fun`) | `ActivationIdempotencyCoordinator`, `CustomerAuthenticationOrchestrator` | Real, official multiplatform support — already used throughout the codebase since M6 |
| `kotlinx.serialization` | `DeviceMetadata`, `CustomerRegisterRequest`, etc. | Real, official multiplatform support — already used since M7.17 |
| `kotlinx.coroutines.flow.Flow`/`StateFlow` | `ConnectivityObserver`, `AuraViewModel.state` | Real, official multiplatform support |
| `androidx.lifecycle.ViewModel`/`viewModelScope` (JetBrains KMP fork) | `AuraViewModel` (base of `ActivationViewModel`) | Real, official Compose Multiplatform KMP support — already the real M6.2 authority |
| `org.jetbrains.androidx.navigation`/Compose Multiplatform UI | `ActivationScreen` and every M9.19 screen | Real, official Compose Multiplatform support |
| `kotlin.random.Random` | Removed from `ActivationViewModel` this milestone (security fix) | N/A — no longer used |
| `java.security.SecureRandom` | `SecureRandomBytes.android.kt` `actual` only | Android-only by design — the `expect` declaration itself is pure `commonMain` |
| `platform.Security.SecRandomCopyBytes` | `SecureRandomBytes.ios.kt` `actual` only | Real, standard Kotlin/Native Apple-platform binding — written, not verified (no macOS/Xcode host) |

## Real, confirmed absence of Android-only classes in `commonMain`

Grepped every new M9 file under `licensing/transport/` and
`ui/activation/` for `android.*`/`androidx.*` imports outside the
already-audited, real KMP-fork exceptions above (`androidx.lifecycle`,
`androidx.compose.*`, both real, official multiplatform artifacts, not
Android-only despite the package prefix) — zero Android-only imports
found. `ConnectivityContract.kt` (M9.16) was specifically audited for
this — confirmed to contain no `android.net.ConnectivityManager`
import, per its own doc's explicit claim.

## Compile-safe adapter skeleton status

`ConnectivityObserver` (`commonMain` interface) has no `iosMain`
implementation yet — real, disclosed, matching the checkpoint's own
instruction that real platform adapter implementation belongs to a
future platform milestone, not M9.

## Honest classification (per the checkpoint's own required categories)

- **iOS compilation from this Windows host**: NOT CLAIMED. No
  macOS/Xcode exists here (`ios-build-readiness-plan.md`, standing
  constraint since M4).
- **iOS linking**: NOT CLAIMED.
- **Simulator execution**: NOT CLAIMED.
- **Device execution**: NOT CLAIMED.
- **Keychain behavior**: NOT CLAIMED — no Keychain code exists in M9
  at all (M10 scope).
- **Real HTTPS iOS behavior**: NOT CLAIMED — no HTTP client exists yet
  (`mobile-http-client-decision.md`).
- **Real iOS activation**: NOT CLAIMED.

## What real, positive evidence exists

Every M9 `commonMain` source file compiles successfully against the
real Android target on this host (`android-activation-build-report.md`),
and every dependency it uses is a real, official Kotlin/Native-
compatible artifact per the table above — the *design* is real and
KMP-first, even though this host cannot verify the Apple-target build
itself. `SecureRandomBytes.ios.kt` is real, intentional, written
Kotlin/Native code (not a stub returning zeros or throwing
`TODO()`) — a genuine, good-faith implementation using the correct,
standard Apple API, submitted for a future Mac build to verify.
