# Secure Storage Current-State Audit (M10.1)

Real audit of every existing storage path in Unified Mobile and the
legacy Android app relevant to M10's own scope.

## Unified Mobile — real, existing, pre-M10 groundwork

### `SecureCredentialStore` (commonMain contract, real, since M2)

`shared/src/commonMain/kotlin/com/actionaura/retail/platform/
PlatformContracts.kt:18-23` — `suspend fun put/get/delete/contains`
over `String key -> ByteArray value`. Its own doc comment: "Android
Keystore / iOS Keychain-Secure Enclave -- Milestone 10." **This
interface was always earmarked for M10 — not discovered fresh, real,
pre-existing groundwork this milestone builds on.**

### `AndroidSecureCredentialStore` (androidMain, real, since M2)

`shared/src/androidMain/kotlin/com/actionaura/retail/platform/
AndroidPlatform.kt:24-51` — real, functional: `EncryptedSharedPreferences`
(AndroidX Security-Crypto) with a `MasterKey` (`AES256_GCM` scheme,
Keystore-backed), `AES256_SIV` key encryption + `AES256_GCM` value
encryption, values Base64-encoded before storage. Its own doc comment:
"this is the Milestone 2 skeleton wiring... not a placeholder." Real,
current gap for M10's own requirements: no associated-data (AAD)
binding per value (Product/Platform/material-type/scope/schema-version
cannot be cryptographically bound to a specific ciphertext with this
library's own API), no atomic multi-key generation/commit/rollback
primitive, and the pinned dependency
(`androidx.security:security-crypto:1.1.0-alpha06`, confirmed in
`mobile-licensing-network-audit.md`, M9) is a real, disclosed **alpha**
release — a real risk for production secret storage. Evaluated in
`android-secure-storage-decision.md`, not silently reused as-is.

### `InstallationIdentityProvider` (commonMain contract, real, since M2)

`PlatformContracts.kt:26-31` — `currentInstallationId()`/
`ensureInstallationKeypair()`. Real, pre-existing seam for M10.11's own
Installation identity generation/storage work — no `androidMain`/
`iosMain` implementation exists yet.

### No other secure-storage-adjacent code exists in Unified Mobile

Grepped `shared/src/` for `SharedPreferences`/`DataStore`/`Keystore`/
`Keychain` — the two files above are the complete real result. No
SQLDelight table stores anything credential-shaped (confirmed:
`InstallationIdentity`/`LocalInstallationSeed`/`ExternalCustomerAccess
Credential`/etc., M7-M9's own types, are pure in-memory Kotlin classes
with no SQLDelight `.sq` schema backing them at all).

## Legacy Android app (`android/aura-retail`, mirrored in `android/aura-clinic`) — real prior art, not reused directly, but real evidence for the M10 design decision

### `DeviceIdentity.kt` / `AndroidKeystoreWrapper` — real, production-proven Keystore usage

`android/aura-retail/app/src/main/java/com/actionaura/retail/licensing/
DeviceIdentity.kt` — a **complete, real, physically-tested** (its own
comments cite "Phase 7V-A" physical device testing) Android Keystore
design:
- Raw `AndroidKeyStore` AES-256-GCM key generation (`KeyGenerator`,
  `KeyGenParameterSpec`), **not** `EncryptedSharedPreferences`.
- Real StrongBox attempt-with-fallback: tries `setIsStrongBoxBacked(true)`
  first, catches `Throwable` (not just an SDK-version check — the
  file's own comment explains a real device without StrongBox hardware
  throws `StrongBoxUnavailableException` *inside* `generateKey()`,
  confirmed via physical testing, which a bare `try/catch` around the
  builder call alone would never catch).
- Storage format: `IV (12 bytes) || ciphertext-with-appended-GCM-tag`,
  written to a real file (`device_key.enc`) under `context.filesDir/
  licensing/` — not `SharedPreferences` at all.
- Real corruption handling: any decrypt failure (`Cipher.doFinal`
  throwing) is caught and re-thrown as `LocalStateCorruptError` — fails
  closed, never silently returns garbage.
- Real "refuse to silently overwrite" policy: `generateNewKey()`
  throws if a key file already exists.
- Public key stored in a **separate, plaintext** file
  (`device_public_key.txt`) — correct, since a public key is not a
  secret.
- Metadata (`algorithm`, `publicKeyFingerprint`, `createdAt`, `status`,
  `ownerInstallationId`) stored as plain JSON (Gson) in a separate
  file — non-secret, real, already-classified-correctly metadata.

**Real, direct evidence basis for `android-secure-storage-decision.md`**
— M10's own Android design is built to mirror this file's own proven
approach (raw Keystore AES-GCM wrapping, StrongBox-with-real-fallback,
decrypt-failure-is-corruption, refuse-silent-overwrite), not
`EncryptedSharedPreferences`, for the real reasons above.

### `CommercialIdentity.kt` — real, correctly-classified non-secret value in plain `SharedPreferences`

`android/aura-retail/app/src/main/java/com/actionaura/retail/identity/
CommercialIdentity.kt:36-42` — stores a random UUID `installationId` in
plain (unencrypted) `SharedPreferences`. **Not a defect**: this value
is explicitly documented as non-secret ("identifies THIS install, not
a customer or license") — a real, correct precedent for M10.3's own
"non-secret security metadata" classification (category D), not
something needing encryption.

### Chaquopy / embedded Python — real, confirmed, not carried into Unified Mobile

`android/aura-retail/app/build.gradle` and `android/aura-retail/app/
src/main/java/com/actionaura/retail/server/ServerBootstrap.kt` confirm
the legacy app really does embed a local Python runtime (Chaquopy).
Already excluded from Unified Mobile since M6's own standing gate ("No
Chaquopy/local Flask introduced"), reconfirmed unchanged through M9 —
no new evidence in M10 changes this.

### Backup / uninstall / upgrade behavior — real, not independently audited this pass

Not independently re-audited in M10.1 (would require reading the
legacy app's own `AndroidManifest.xml` `android:allowBackup`/
`fullBackupContent` configuration, out of this file's own read scope)
— real, open item folded into `secure-storage-backup-reinstall-
policy.md` (M10.19), which defines the *required* policy for Unified
Mobile going forward rather than depending on the legacy app's own
undocumented behavior.

## Per-value real documentation (per M10.1's own required table)

| Value | Current location | Current encryption | Access lifetime | Backup behavior | Logging | Required M10 destination | Migration | Deletion | Discovered defect |
|---|---|---|---|---|---|---|---|---|---|
| Legacy device Ed25519 private key | `android/aura-retail` app-private file (`device_key.enc`) | Real AES-256-GCM, Keystore-wrapped | Until `destroyKey()` | Not audited (legacy app, out of Unified Mobile scope) | None found | N/A — legacy app, not migrated by Unified Mobile | N/A | `destroyKey()`, real | None — real, correct design |
| Legacy installation UUID | `android/aura-retail` `SharedPreferences` (plaintext) | None (correctly, non-secret) | Permanent until app data cleared | Not audited | None found | N/A — legacy app | N/A | Not implemented (legacy) | None — correctly classified non-secret |
| Unified Mobile `ExternalCustomerAccessCredential`/`RefreshCredential` (M9) | In-memory only (`ExternalCustomerSession`, held by `ActivationViewModel`) | N/A (never persisted yet) | Process lifetime only | N/A | None (redacted `toString()`, M9.23) | `SecureMaterialStore` (M10.4), Customer-session category | New, no prior persisted state | Real, defined in M10.18 | None — M9 never persisted it, exactly as scoped |
| Unified Mobile Installation credential / signed lease (M9) | In-memory only, `NoSecureStorageAvailableSink` always fails | N/A | Process lifetime only | N/A | None (redacted) | `SecureMaterialStore`, Installation-credential/lease categories | New | Real, defined in M10.18 | None |
| `AndroidSecureCredentialStore` (M2 skeleton) | `EncryptedSharedPreferences` (alpha dependency) | Real AES-256-SIV/GCM via Keystore-backed `MasterKey` | Indefinite, no rotation/versioning | Not audited | None found | Evaluated, real gaps documented (`android-secure-storage-decision.md`) | N/A — never used to store real M10 material yet | Real `delete()` exists, no atomicity | Alpha dependency; no AAD binding; no generation/atomic-commit support |

No value was found stored as plaintext where a secret classification
would apply — the audit found either (a) real, already-encrypted
legacy prior art, (b) correctly-classified non-secret plaintext, or
(c) not-yet-persisted M9 in-memory-only state. No pre-existing defect
of "secret stored in plaintext" exists to fix in M10 — M10's own work
is net-new secure persistence, not a remediation of an existing leak.
