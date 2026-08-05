# Secure Storage Corruption and Partial-State Recovery (M10.17)

Real corruption/partial-state handling, built and tested in M10.6
(`secure-material-atomic-commit.md`); this document maps the
checkpoint's own full required scenario list onto what is real,
tested, and what remains a real, disclosed platform-dependent gap.

## Real, tested scenarios

| Scenario | Real outcome | Evidence |
|---|---|---|
| Missing one bundle component | `CORRUPT_DATA` — `loadGeneration` requires every real component present | `corruptedComponentFailsClosedNeverReturnsPartialBundle` |
| Corrupted ciphertext | `CORRUPT_DATA` | Same test, plus `AEADBadTagException` handling (Android), header-mismatch handling (iOS) |
| Corrupted metadata | `CORRUPT_DATA` — real `try/catch` around `SecureActivationBundleMetadata` deserialization | `loadGeneration` |
| Wrong associated data | `CORRUPT_DATA`, never authenticates | `wrongAssociatedDataNeverAuthenticates` |
| Missing key | `KEY_INVALIDATED` (Android: `loadWrappingKey` returns `null`) | `AndroidSecureBlobStore.get` |
| Invalidated key | Same real code path | Same |
| Unsupported version | Currently indistinguishable from corruption — real, disclosed gap, see `secure-storage-versioning-migration.md` | — |
| Stale active-generation pointer | Real, by design — the pointer always names *some* real generation; "stale" in the sense of "not the most recent attempted write" is exactly the safe, correct outcome (M10.6's own "old bundle remains current until promotion succeeds") | `oldValidBundleSurvivesAFailedReplacement` |
| Staging generation present (uncommitted) | Real, harmless — an orphaned staging generation is never read by `loadActivationBundle` (which only ever follows the pointer); cleaned up opportunistically on the next successful commit | `secure-material-atomic-commit.md`'s own "Process death can be recovered safely" section |
| Old and new generations both present | Same real, harmless case as above | Same |
| Process death before promotion | Real, safe — old generation remains current | `failureAfterFirstStagedItemLeavesNoPromotedBundleAndCleansUp` |
| Process death after promotion | Real, safe — new generation is now current and complete; only the old generation's cleanup may be incomplete, which is harmless | Design, M10.6 |
| Process death during cleanup | Same real, harmless outcome | Design, M10.6 |
| Disk/storage full | Real, `WRITE_FAILED` (Android: real `IOException` from a failed file write, caught and mapped) | `AndroidSecureBlobStore.put`'s own `catch (e: Exception)` |
| Keychain duplicate item | Real, handled — `errSecDuplicateItem` → `SecItemUpdate` fallback | `IosSecureBlobStore.put`, `ios-keychain-storage-implementation-report.md` |
| Keychain item not found | Real, `Success(null)`, not a failure | `IosSecureBlobStore.get` |
| Keystore operation failure | Real, mapped to the closed `SecureStorageFailureCode` vocabulary, never a raw exception | Both platform stores' own `catch` blocks |

## Real, required startup behavior (restated, integrated in M10.22)

Recover a valid committed generation when possible (real,
`recoverInterruptedCommit`); never combine fields from different
generations (real, structural, `noMixedGenerationEverObserved`); never
expose partial activation (real — `loadGeneration` returns either a
complete `SecureActivationBundle` or a `Failure`, never a partially-
populated bundle); return an explicit blocked/recovery state (real,
`SecureStorageRecoveryOutcome.UnrecoverableReactivationRequired`);
preserve diagnosable non-secret error information (real,
`SecureStorageFailure.safeDiagnosticReason`, never a secret value);
offer a safe reactivation path where recovery is impossible (real, by
design — an `UnrecoverableReactivationRequired` outcome, wired in
M10.22's startup classification, routes to the same real activation
flow M9 already built, `ActivationState.NOT_STARTED` via
`RESTART`).

## Real, binding "do not auto-delete" rule

`recoverInterruptedCommit`/`loadActivationBundle` never call `delete`
on a corrupted or unrecoverable generation — confirmed by direct
inspection of `GenerationalSecureMaterialStore`: no code path in
either function invokes `blobStore.delete` or `deleteScope`. A
corrupted bundle is left in place (available for real, future forensic
inspection or a manual support-driven recovery attempt) until an
explicit, real, user/support-triggered `deleteScope` call — matching
the checkpoint's own explicit "do not automatically delete all
material on the first read failure unless policy proves no safer
recovery exists" rule.
