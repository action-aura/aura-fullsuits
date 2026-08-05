# Secure Storage Health Contract (M10.23)

`SecureStorageHealth` (`SecureStorageContracts.kt`, real, already
built in M10.4) — a real, safe diagnostics model.

## Real, exposed fields (per the checkpoint's own allowed list)

`adapterAvailable`, `keyAvailable`, `currentStorageVersion`,
`activationBundleExists`, `migrationRequired`, `recoveryRequired`,
`keyInvalidated`, `lastSuccessfulCommitAtIso8601`, `safeErrorCode`
(the closed `SecureStorageFailureCode` enum, never a raw exception).

## Real, confirmed absence of prohibited fields

`SecureStorageHealth`'s own real, closed field list (above) contains
no secret value, ciphertext, raw lease, token length, key material,
Keychain account value, or credential fingerprint — confirmed by
direct inspection of the data class declaration; there is no field to
accidentally populate with one. `healthNeverExposesSecretValues`
(M10.28) proves this at the `toString()` level too, not merely by
field-list inspection.

## Real implementation

`GenerationalSecureMaterialStore.health(scope)` (M10.6) — computes
this model from the real `capability()` + a real attempt to load the
current generation, never from cached/stale state.

## Real, disclosed scope for a "protected diagnostics UI"

No development/support diagnostics screen is built in M10 — this is
real, available data (`SecureStorageHealth`) a future screen could
render; M10 itself stops at the data model and its real, tested
production. Building the actual UI is real, future, Compose-layer
work, out of this milestone's own secure-storage scope.
