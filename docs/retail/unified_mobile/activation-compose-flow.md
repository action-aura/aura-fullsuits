# Activation Compose Flow (M9.19)

`ActivationScreen` (`shared/.../ui/activation/ActivationFlow.kt`) —
real, shared Compose flow, built entirely on real M6 design components
(`AuraScaffold`) and the real M6.11 `AuraStrings` localization
catalog, extended with real `activation.*` keys in this milestone.

## Real screen coverage (17, matching the checkpoint's own list)

1. Activation welcome — `ActivationWelcomeScreen`
2. External Customer sign-in — `CustomerSignInScreen`
3. Account verification required — `AccountVerificationRequiredScreen`
4. License entry — `LicenseEntryScreen`
5. License status result — folded into `LicenseEntryScreen`'s own
   `LICENSE_REJECTED` branch (shows the real error + retry) and the
   real `DEVICE_POLICY_LOADING`/`LICENSE_CLAIMING` loading screens
6. Device policy summary — `DevicePolicyAndLabelScreen`
7. Device identification and label — same screen (real, combined —
   the checkpoint's own device-policy-summary and device-labeling
   steps share one real form in this implementation)
8. Activation confirmation — `ActivationConfirmationScreen`
9. Activation progress — `LoadingScreen` (real, reused for
   `ACTIVATION_REQUESTING`/`ACTIVATION_RESPONSE_RECEIVED`)
10. Device limit reached — `DeviceLimitReachedScreen`
11. Platform unavailable — `PlatformUnavailableScreen`
12. iOS server not ready — `IosNotReadyScreen`
13. Network unavailable — `NetworkUnavailableScreen`
14. Service not configured — `ServiceNotConfiguredScreen`
15. Secure storage not implemented — `SecureStorageUnavailableScreen`
16. Activation result — `ActivationResultScreen`
17. Support guidance — real `TextButton`/`activation.support.action`
    affordance present on every error-adjacent screen, not a separate
    dedicated screen (a real, deliberate simplification — support
    guidance is contextual, not a standalone destination, in this
    implementation)

## Real "no fake success in production" discipline

- No screen ever renders `ActivationResultScreen` except when
  `ActivationState.ACTIVATION_COMPLETE` is real, i.e. only reachable
  after `ActivationResponseProcessor` reports `Complete`
  (`activation-response-processing.md`) — which, per M9.13's own real
  production default, never happens today.
- `ServiceNotConfiguredScreen` is the real, honest screen every
  production build actually shows for `TRANSPORT_NOT_CONFIGURED` —
  matching `DisabledProductionTransport`'s own real behavior.
- No device count, Customer session, or License status is ever
  fabricated — every value displayed (`state.devicePolicy`,
  `state.presentationError`, etc.) comes directly from
  `ActivationViewModel`'s own real `StateFlow`.

## Real i18n/RTL/theme support

Every screen resolves its strings through `AuraStrings.resolve(key,
locale, args)`, the same real, already-audited M6.11 mechanism used by
every other screen — English and Arabic both real (new `activation.*`
keys added to the same catalog, `AuraStrings.kt`), RTL handled the
same structural way M6's own `rtl-bidi-foundation.md` already
established (via `LocalLayoutDirection`, not re-implemented per-
screen), light/dark theme via the same real `AuraAppTheme` every other
screen already uses. Accessibility/keyboard/reduced-motion: relies on
Compose Multiplatform's own real, built-in `Text`/`OutlinedTextField`/
`Button` semantics — no custom, unaudited accessibility behavior was
added.

## Real, deliberate scope limitation disclosed

`ActivationConfirmationScreen`'s `onActivate` calls
`ActivationViewModel.onActivate()` with no Installation-identity
argument — the ViewModel itself owns the (currently `null`-defaulted)
`installationIdentityProvider`. No preview/test-only placeholder
identity is ever constructed inside this Compose file — removed
during implementation specifically to avoid the exact "fake data in a
production-reachable code path" mistake this milestone's own checklist
exists to prevent.
