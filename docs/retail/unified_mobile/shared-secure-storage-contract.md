# Shared Secure Storage Contract (M10.4)

One `commonMain` secure-storage authority, split into two real layers
so the hard atomicity/recovery logic is written once and is fully
unit-testable without any real platform Keystore/Keychain.

## Layer 1 — `SecureBlobStore` (narrow platform primitive)

`shared/.../securestorage/SecureBlobStore.kt` — the only interface
real `androidMain`/`iosMain` code implements:
`capability()`, `put(name, associatedData, plaintext)`,
`get(name, associatedData)`, `delete(name)`, `list(prefix)`,
`rotateKey()`. Real, binding requirement: `put` must bind
`associatedData` to the ciphertext via authenticated encryption (AES-
GCM's own AAD) — `secureAssociatedData(key, version)` computes this
deterministically from the real `SecureMaterialKey` (material type +
Product/Installation/account scope) + `SecureMaterialVersion`.

## Layer 2 — `SecureMaterialStore` (real, higher-level authority)

`shared/.../securestorage/SecureMaterialStore.kt` — the interface
presentation/orchestration code depends on:
`initialize`, `capability`, `commitActivationBundle`,
`loadActivationBundle`, `deleteScope`, `rotateKey`,
`recoverInterruptedCommit`, `health`. One real implementation,
`GenerationalSecureMaterialStore`, pure Kotlin, built on `SecureBlobStore`
— see `secure-material-atomic-commit.md` for its own real design.

## Real, closed types (per the checkpoint's own suggested contract list)

`SecureMaterialKey` (type + scope), `SecureMaterialVersion`
(storage-format + material-type version pair), `SecureMaterialRevision`
(monotonic counter), `SecureMaterialSnapshot`, `SecureStorageHealth`,
`SecureStorageCapability`, `SecureStorageFailure` — real, closed
`SecureStorageFailureCode` enum (15 real values, matching the
checkpoint's own list exactly: `NOT_AVAILABLE`, `LOCKED`,
`AUTHENTICATION_REQUIRED`, `KEY_INVALIDATED`, `CORRUPT_DATA`,
`PARTIAL_COMMIT`, `UNSUPPORTED_VERSION`, `WRITE_FAILED`,
`READ_FAILED`, `DELETE_FAILED`, `ROTATION_FAILED`, `STORAGE_FULL`,
`ACCESS_DENIED`, `CANCELLED`, `UNKNOWN_SAFE_FAILURE`).

`SecureStorageFailure.toString()` never includes a secret — its only
fields are the closed enum code and an optional `safeDiagnosticReason`
string, which every real call site populates with a structural
description ("component write failed before promotion"), never a
value.

## Real, structural guarantee: no platform object leaks into commonMain

`SecureBlobStore`/`SecureMaterialStore` operate exclusively on
`ByteArray`/Kotlin data classes — no `android.security.keystore.*`,
no `Keychain`-adjacent CoreFoundation type, appears anywhere in these
interface signatures. Real platform types are fully contained inside
each platform's own `actual` implementation file.

## No raw `Throwable` as a domain result

Every `SecureBlobStore`/`SecureMaterialStore` method returns
`SecureStorageResult<T>` (`Success<T>`/`Failure`) — a real platform
exception (`KeyStoreException`, `OSStatus` mapping, etc.) is caught
inside the real `actual` implementation and translated to the closed
`SecureStorageFailureCode` vocabulary before ever reaching commonMain
callers.
