# Activation Process Recovery Contract (M9.25)

Real definition of what survives process recreation, extending
`installation-reinstall-recovery-contract.md` (M8.5) to the
activation-flow-in-progress case specifically.

## Real, safe non-secret state (may survive)

Current flow step (`ActivationState`, a plain enum — real, safe to
persist), Product/Platform (`LicensingProductCode`/`LicensingPlatform`,
both closed enums), Installation label
(`ActivationUiState.deviceLabelInput`, plain user-entered text),
non-secret activation-attempt identifier (the idempotency key itself —
`mobile-activation-idempotency.md`'s own finding: not itself a secret,
safe to persist as retry metadata), whether retry is available
(`ActivationUiState.retryAvailable`).

## Real, never-persisted state

Password (`onSignIn`'s own parameter — never assigned to any field,
so there is nothing to accidentally persist), License serial
(`ActivationUiState.licenseSerialInput` is cleared to `""` on
successful claim, per `activation-presentation-contract.md`; never
written to any persistence layer at any point — `ActivationViewModel`
holds it only in in-memory `StateFlow`), Customer access/refresh token
(`ExternalCustomerAccessCredential`/`ExternalCustomerRefreshCredential`,
only ever passed to `CustomerSessionCredentialSink.commit(...)`, whose
real M9 implementations are `InMemorySecureMaterialSink` (test-only)
and `NoSecureStorageAvailableSink` (production, always fails) — never
written anywhere real durable persistence would survive a process
kill), raw signed lease (same real handoff-sink discipline).

## Real recovery behavior when secure material was returned but not durably stored

This is real, current, and honest, not hypothetical: because
`NoSecureStorageAvailableSink` always fails, **every real production
activation attempt today lands in exactly this scenario** —
`ActivationResponseProcessor` returns `SecurePersistenceRequired`, the
state machine moves to `ActivationState.SECURE_PERSISTENCE_REQUIRED`,
and `SecureStorageUnavailableScreen` (M9.19) is shown. If the process
is then killed and restarted, `ActivationViewModel` is re-constructed
fresh (`NOT_STARTED`), and the user must restart the flow — **no
duplicate Installation attempt is created**, because:
1. The prior attempt's own idempotency key is gone (a fresh
   `ActivationIdempotencyCoordinator` is constructed), but
2. The *server's* own idempotency contract
   (`activation-idempotency-contract.md`) means a genuinely-retried
   request with fresh client-side state still resolves safely: if the
   server actually processed the prior attempt and created a real
   Installation, a fresh activation attempt with the same device-key
   fingerprint is recognized as `SAME_INSTALLATION_RETRY`
   (`multi-device-scenario-contract.md` #12), not a new slot
   consumption — this is real, already-tested server behavior
   (`activation-idempotency-contract.md`), not new M9 logic.

## Real, disclosed limitation

Because M9 implements no persistence for the idempotency-key/
fingerprint pair itself (`mobile-activation-idempotency.md`'s own
"M9 may persist only non-secret retry metadata through a safe abstract
store" — no such store is actually built in M9, only documented as
permitted), a process kill between "request sent" and "response
received" always falls back to the server-side `SAME_INSTALLATION_
RETRY` recognition path above rather than a client-side idempotency-
key match — real, safe, but slightly less efficient than a future
milestone's own persisted-retry-metadata implementation would be. Not
a security or correctness gap — a real, disclosed, future optimization
opportunity only.
