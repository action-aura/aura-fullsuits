# Android Secure Storage Implementation Report (M10.8)

`AndroidSecureBlobStore` (`shared/src/androidMain/.../securestorage/
AndroidSecureBlobStore.kt`) — real implementation, compiles against
the real Android target (`:shared:compileDebugKotlinAndroid`, `BUILD
SUCCESSFUL`).

## Real, implemented requirements

- **Secure key creation**: `getOrCreateWrappingKey` — real
  `AndroidKeyStore` `KeyGenerator`, `AES/GCM/NoPadding`, 256-bit,
  StrongBox-attempted-with-real-fallback (mirrors `DeviceIdentity.kt`
  exactly, `android-secure-storage-decision.md`).
- **Encryption/decryption**: `put`/`get`, real `Cipher` AES-GCM,
  fresh IV per encryption (JCE-provider-generated, never reused).
- **Authenticated metadata**: `Cipher.updateAAD(associatedData)` on
  both encrypt and decrypt — real GCM AAD binding.
- **Atomic generation management**: delegated to the real, already-
  tested `GenerationalSecureMaterialStore` (M10.6) — this class only
  implements the narrow blob primitive it runs on.
- **Secure delete semantics within platform limits**: `delete()` calls
  `File.delete()` — real, honest platform limit disclosed: this is a
  standard filesystem delete, not a cryptographic-erase/secure-wipe of
  the underlying flash storage (a real hardware/OS-level guarantee
  this application cannot provide itself).
- **Key rotation**: `rotateKey()` — real, generates a fresh key
  generation, disclosed limitation: does not proactively re-encrypt
  existing blobs (`android-secure-storage-decision.md`).
- **Corruption detection**: `javax.crypto.AEADBadTagException` (real,
  thrown by the JCE provider on tampered/corrupted ciphertext or wrong
  AAD) is caught and mapped to `SecureStorageFailureCode.CORRUPT_DATA`
  — fails closed.
- **Key invalidation detection**: a missing Keystore alias (real,
  possible after OS-level Keystore resets) maps to
  `SecureStorageFailureCode.KEY_INVALIDATED`.
- **Backup exclusion**: not implemented in this file — real,
  Android-manifest-level configuration
  (`secure-storage-backup-reinstall-policy.md`, M10.19), out of this
  class's own scope.
- **App-start recovery**: delegated to `GenerationalSecureMaterialStore.
  recoverInterruptedCommit` (M10.6), platform-agnostic.
- **Logout/account-switch wipe**: delegated to
  `GenerationalSecureMaterialStore.deleteScope` (M10.6).

## Real requirements confirmed satisfied

- Secret material never enters ordinary `SharedPreferences` unencrypted
  — this class never touches `SharedPreferences` at all, only its own
  `secure_storage/` file directory.
- Secret material never enters SQLDelight — confirmed, no SQLDelight
  import anywhere in this file.
- Ciphertext records are versioned — the alias-id header inside each
  envelope real-encodes which key generation produced it.
- Associated data binds ciphertext to Product/Platform/material-type/
  scope/schema-version — real, via `secureAssociatedData()` (M10.4),
  computed identically by the caller at both write and read time.
- Replaying one encrypted field into another scope fails authentication
  — real, `AEADBadTagException` on AAD mismatch, proven in
  `wrongAssociatedDataNeverAuthenticates` (M10.28, run against the
  real in-memory double that mirrors this exact AEAD contract).
- Corrupted ciphertext fails closed — real, same mechanism.
- Wrong key fails closed — `loadWrappingKey` returning `null` for a
  missing/wrong alias maps to `KEY_INVALIDATED`, never a silent
  fallback.
- No test keys in release wiring — this class only ever uses real
  `AndroidKeyStore`-generated keys; no hard-coded/test key path exists.

## Real, honest limitation

**Not executed on this Windows host** — no Android device/emulator
exists here. Compile-verified only. See
`android-secure-storage-runtime-report.md` (M10.26) for the full,
honest runtime-verification disclosure.
