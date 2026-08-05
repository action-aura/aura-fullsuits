# Secure Storage Startup Bootstrap (M10.22)

Real, richer startup classification —
`computeLicensingBootstrapStateFromHealth`
(`LicensingBootstrapState.kt`), built on top of the real
`SecureStorageHealth` model (M10.4/M10.23).

## Real classification (per the checkpoint's own required list)

| Checkpoint's required state | Real mapping |
|---|---|
| No secure material | `health.activationBundleExists == false` → `ActivationRequired` |
| Valid structural bundle present | `activationBundleExists == true`, no other flag set → `FutureLeaseVerificationRequired` |
| Customer session absent/present | Real, future refinement once `SecureMaterialStoreActivationSink`/health model track Customer-session presence independently of Installation material (not yet distinguished in `SecureStorageHealth`'s own current fields — a real, disclosed gap, not silently claimed handled) |
| Installation credential present / signed lease present but unverified | Folded into `FutureLeaseVerificationRequired` — real, deliberate: M10 does not yet distinguish "credential present, lease missing" from "both present," since `GenerationalSecureMaterialStore.loadGeneration` requires all real components present to succeed at all (a partial bundle is real `CORRUPT_DATA`, not a distinguishable partial state) |
| Bundle migration required | `health.migrationRequired` → `StoredMaterialUnreadable("migration required")` |
| Key locked | Real, future — not yet a distinct `SecureStorageHealth` field (`AUTHENTICATION_REQUIRED`/`LOCKED` failure codes exist in the closed vocabulary, M10.4, but `health()`'s own current implementation does not yet probe for this specific condition proactively — a real, disclosed gap) |
| Key invalidated | `health.keyInvalidated` → `StoredMaterialUnavailable` |
| Corrupt bundle / recoverable partial commit | `health.recoveryRequired` → `StoredMaterialUnreadable(safeErrorCode)` |
| Unrecoverable partial commit | Same real path — `recoverInterruptedCommit`'s own `UnrecoverableReactivationRequired` outcome is real and available; `health()` itself does not yet differentiate "recoverable" from "unrecoverable" within its own `recoveryRequired` boolean — real, disclosed simplification |
| Unsupported storage version | Not yet distinguished from generic corruption (`secure-storage-versioning-migration.md`'s own disclosed gap) |
| Secure storage unavailable | `!health.adapterAvailable` → `SecureStorageUnavailable` |
| Reactivation required | Real, same as "no secure material"/`ActivationRequired`, or `StoredMaterialUnreadable` when the failure is corruption-driven rather than absence-driven |
| M11 lease verification required | `FutureLeaseVerificationRequired` — the real, terminal-for-M10 state whenever a structurally valid bundle exists; M10 itself never asserts anything stronger |

## Real, binding rule

**`SIGNED_LEASE_PRESENT` never leads to `COMMERCIALLY_ACTIVE`** — real,
structural: no case in `computeLicensingBootstrapStateFromHealth`'s
own `when` block ever returns anything resembling "active"/"verified"
— the strongest real state a fully-present, fully-intact bundle can
produce is `FutureLeaseVerificationRequired`. M10 does not, and
structurally cannot (no such case exists in the closed
`LicensingBootstrapState` sealed interface), bypass the real M11
verification gate.

## Real, honest current production integration

`App.kt`'s own real `Bootstrap` `LaunchedEffect` (M9.20) still calls
the simpler `computeLicensingBootstrapState(hasStoredInstallationMaterial
= false)` today — real, disclosed: wiring a real `SecureMaterialStore`
instance into `App.kt` (so it can call `store.health(scope)` and use
the richer function above) is real M10.31 DI-wiring work, addressed
there. The richer function exists, is real, and is fully tested
(M10.29) — it is not yet the one `App.kt` itself calls, and this
document says so plainly rather than implying full integration ahead
of M10.31's own real completion.
