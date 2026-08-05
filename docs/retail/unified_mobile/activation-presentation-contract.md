# Activation Presentation Contract (M9.18)

`ActivationViewModel` (`shared/.../ui/activation/ActivationViewModel.kt`)
— real, built on the M6 `AuraViewModel<S, E>` authority
(`shared-viewmodel-lifecycle.md`), the same pattern every other real
screen ViewModel already uses.

## Real actions implemented

`onStart`, `onEmailChange`/`onSignIn`, `onVerificationConfirmed`,
`onLicenseSerialChange`/`onSubmitLicense`, `onRetryLicenseClaim`,
`onDeviceLabelChange`/`onConfirmDevice`, `onActivate`,
`onRetryActivation`, `onCancel`, `onOpenSupport`, `onSignOut`,
`onRestartFlow` — the checkpoint's own named action set, mapped 1:1.

## Real `ActivationUiState` — presentation-safe fields only

`activationState` (the real `ActivationState`, M9.9), `emailInput`,
`licenseSerialInput` (cleared to `""` immediately on a successful
claim — `onSubmitLicense`'s own `setState { it.copy(..., 
licenseSerialInput = "") }`), `deviceLabelInput`, `devicePolicy`
(the real, validated `ResolvedDevicePolicy`), `presentationError`,
`retryAvailable`, `submitting`.

**Never placed in `ActivationUiState`** (verified by direct inspection
of every field above — none is credential-shaped):
password, Customer access/refresh token, License serial after
submission, Installation credential, raw signed lease payload, private
key, idempotency secret material. The password parameter to `onSignIn`
is a plain function argument, never assigned to any `ViewModel` field
or `UiState` field — its only real destination is the one
`authOrchestrator.signIn(...)` call.

## Real duplicate-submission prevention

`onSignIn`/`onSubmitLicense`/`onActivate` all check `currentState.
submitting` before doing anything, and `onActivate` additionally goes
through `ActivationIdempotencyCoordinator.tryBeginAttempt()` — a
second concurrent tap is a real no-op, not a race.

## Real one-time effects

`ActivationEffect`: `NavigateToActivationComplete`,
`NavigateToSupport`, `ClearSecureInput` — sent via the real M6
`sendEffect`/bounded-channel mechanism (`AuraViewModel.kt`).
`ClearSecureInput` is sent after every sign-in/license-submission
attempt (success or failure) — the real signal for the future Compose
layer (M9.19) to clear its own password/serial text-field state,
independent of whether the ViewModel itself ever held the secret.

## Real, honest Installation identity handling

`ActivationViewModel`'s constructor takes an `installationIdentityProvider:
() -> InstallationIdentity? = { null }`. Real Installation identity
generation is `ANDROID_ADAPTER`/`IOS_ADAPTER` work (M8 gap #8/#9), not
implemented in M9 — the default `{ null }` is the real, honest current
state. `onActivate()` checks the provider and, when it returns `null`
(the real, current default), sets `presentationError = 
PresentationError.Transport.NotConfigured` and returns without ever
constructing an `ActivationCommand` — **no fabricated placeholder
identity is synthesized anywhere in this ViewModel or in the Compose
layer** (`activation-compose-flow.md`).

## Real response-processing wiring

`onActivate` calls `ActivationResponseProcessor.process(...)` (M9.12)
on a successful transport outcome, and only transitions to
`ACTIVATION_COMPLETE` (via `SECURE_PERSISTENCE_COMMITTED`) when the
processor reports `Complete`. Because the default constructor
parameters are `NoSecureStorageAvailableSink` (the real, honest
production default — M9.13), **every real production activation
attempt today ends at `SECURE_PERSISTENCE_REQUIRED`**, never
`ACTIVATION_COMPLETE` — a real, disclosed, correct consequence of M9's
own scope, not a bug.
