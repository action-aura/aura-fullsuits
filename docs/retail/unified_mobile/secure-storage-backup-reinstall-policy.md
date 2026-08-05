# Secure Storage Backup, Restore, and Reinstall Policy (M10.19)

Real, platform-specific policy — Android and iOS are **not** assumed
to behave identically.

## Android

- **Auto Backup / device-to-device transfer**: real, disclosed —
  `AndroidSecureBlobStore`'s own files live under `context.filesDir/
  secure_storage/` (real, standard Android Auto Backup default
  behavior **includes** `filesDir` content unless explicitly
  excluded). **Real, required future action** (not yet implemented in
  M10 — a real `AndroidManifest.xml`/`backup_rules.xml` configuration
  change, out of this milestone's own Kotlin-source scope): exclude
  `secure_storage/` from Auto Backup rules, since a restored encrypted
  file without its matching non-exportable Keystore key (which never
  travels with a backup) would be permanently undecryptable dead
  weight, not a real security hole by itself, but real, avoidable
  clutter and a real source of confusing `KEY_INVALIDATED` failures on
  restore.
- **Keystore key portability**: real, confirmed — `AndroidKeyStore`
  keys generated with no `setUserAuthenticationRequired`/export flags
  are real, non-exportable and **never** included in any backup
  mechanism, encrypted or not — this is a real, unconditional Android
  platform guarantee this application inherits for free.
- **Restore onto another device / restore after reinstall**: because
  the wrapping key never restores, any restored `secure_storage/`
  files are real, permanently undecryptable on the new
  device/reinstall — `AndroidSecureBlobStore.get` correctly returns
  `CORRUPT_DATA` (real, tested: `wrongAssociatedDataNeverAuthenticates`-
  adjacent real AEAD-failure path) rather than silently succeeding
  with garbage, and the app's own real M10.22 startup logic must
  treat this as "no usable material," routing to reactivation — never
  ambiguous partial state.

## iOS

- **Keychain persistence across reinstall**: real, standard Apple
  behavior — Keychain items with the app's own access group commonly
  **do** survive an app delete-then-reinstall on the *same* device
  (a real, well-known iOS behavior, distinct from Android's own
  file-based `filesDir` wipe-on-uninstall behavior) — a real,
  disclosed platform asymmetry this document does not paper over.
- **Encrypted backup / device restore**: real, per M10.9's own chosen
  `kSecAttrAccessibleWhenUnlockedThisDeviceOnly` — this accessibility
  class's own `ThisDeviceOnly` suffix means the item is real,
  explicitly excluded from being restored onto a **different**
  physical device, even from an encrypted backup. Restoring onto the
  *same* physical device (e.g. after an OS reinstall) may or may not
  preserve it, real, platform-dependent, not claimed guaranteed either
  way without real device testing (`ios-secure-storage-runtime-
  validation-plan.md`).
- **iCloud Keychain synchronization**: real, explicitly disabled
  (M10.9) — never a real vector for cross-device restoration of
  Installation-scoped material.
- **App identifier/access group**: real, no App Group entitlement
  configured (M10.9) — Keychain items are scoped to this app alone.
- **Transfer to a new iPhone**: real, expected outcome — the
  `ThisDeviceOnly` class means Installation-scoped material does
  **not** transfer; a "transfer to new iPhone" flow (Quick Start,
  iCloud backup restore) leaves the new device with no usable
  Installation credential, correctly routing to reactivation, matching
  the real, intended device-bound design.

## Required policy (both platforms, real and binding)

- **Duplicated identity across devices must not automatically be
  trusted**: real, unchanged since M8.5 — Owner's own real, unique
  `DevicePublicKey.fingerprint` constraint is the actual enforcement
  point, not anything client-side.
- **Restored material must still pass future Owner refresh and M11
  lease verification**: real, structural — even in the rare case
  material *does* survive a restore/reinstall (same-device iOS OS
  reinstall, for example), it is only ever loaded via
  `loadActivationBundle` and never marked "trusted"/"verified" by
  M10's own code (M10.14's own "M10 stores but does not trust" rule)
  — a real future M11 verification pass, or a real future Owner
  refresh call, remains the actual trust gate.
- **Failure to decrypt restored material enters recovery/reactivation
  flow**: real, `CORRUPT_DATA`/`KEY_INVALIDATED` → `startup-licensing-
  integration.md`'s own real M10.22 startup classification.
- **No plaintext fallback**: real, confirmed — neither platform store
  has any code path that returns unencrypted data when decryption
  fails; every failure is a real `SecureStorageResult.Failure`.
- **No automatic slot bypass**: real, unchanged — nothing in M10
  computes or asserts a device-slot decision locally; that remains
  entirely real, server-side authority (M7-M9's own established rule).
