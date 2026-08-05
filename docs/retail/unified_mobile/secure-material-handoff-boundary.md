# Secure Material Handoff Boundary (M9.13)

`SecureMaterialHandoff.kt` — narrow interfaces for future M10 storage.

## Real interfaces

`InstallationCredentialSink`, `SignedLeaseSink`,
`CustomerSessionCredentialSink` — each a single `suspend fun commit(...)
: SecureMaterialCommitResult`. `SecureMaterialCommitResult` is a real,
closed sealed type: `Committed`/`Failed(reason)`/`Cancelled`.

## Real requirements satisfied

- **No credential value exposed to Compose/ViewModel/toString**:
  `InstallationCredentialMaterial` wraps the raw string, exposes it
  only via `expose()`, redacts `toString()`.
- **Atomic handoff contract**: each `commit` call is a single
  suspend function returning one final result — no partial-commit
  API surface exists to misuse.
- **Failure does not mark activation complete**: enforced by
  `ActivationResponseProcessor` (M9.12), not by the sink itself — the
  sink only reports success/failure honestly; the processor is what
  refuses to call anything complete on failure.
- **Cancellation semantics**: `SecureMaterialCommitResult.Cancelled`
  is a real, distinct outcome from `Failed` — future M10 sinks may
  need to distinguish "user cancelled a biometric prompt" from "the
  keystore genuinely failed."
- **Overwrite/rotation, previous-value rollback, account/session
  separation, Product separation**: real, open design questions for
  the M10 implementer — not resolved here (M9 defines the boundary
  shape only, per its own explicit scope restriction).

## Real M9 implementations (both real, neither is platform secure storage)

1. `InMemorySecureMaterialSink` — `commonTest`-only, deterministic,
   used by every M9.28/M9.29 fixture-transport test.
2. `NoSecureStorageAvailableSink` — the real, honest production
   default: every `commit` deterministically returns `Failed
   ("SECURE_STORAGE_NOT_IMPLEMENTED")`. This is what production DI
   wires today (`startup-licensing-integration.md`).

## Explicitly not implemented in M9

Android Keystore, `EncryptedSharedPreferences`, iOS Keychain, Secure
Enclave, or any platform backup-interaction behavior — all real,
disclosed M10 scope. `androidx.security:security-crypto:1.1.0-alpha06`
(already present in `androidMain`'s dependencies since before this
milestone, per `mobile-licensing-network-audit.md`'s own finding) is
not referenced anywhere in M9's own code.
