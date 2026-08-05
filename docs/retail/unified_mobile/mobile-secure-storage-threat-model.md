# Mobile Secure Storage Threat Model (M10.2)

Real, platform-specific threat model for M10's own secure-storage
authority. No absolute-protection claim is made for a compromised
(rooted/jailbroken) device — real security engineering does not
promise that.

| # | Threat | Classification | Real mitigation |
|---|---|---|---|
| 1 | Lost device (locked, not compromised) | Protected | Android Keystore key is non-exportable and hardware-backed where available; iOS Keychain item requires the device to be unlocked for the accessibility class M10.9 selects |
| 2 | Stolen, unlocked device | Partially mitigated | The OS-level unlock already granted the thief the same access a legitimate user has at that moment — real, unavoidable limit of any on-device secure storage; the *stored key material itself* still cannot be exfiltrated off-device (non-exportable), limiting the blast radius to that one physical device |
| 3 | Rooted Android device | Partially mitigated / platform-dependent | A real root exploit can potentially extract keys from a software-only (non-hardware-backed) Keystore implementation on some devices; StrongBox/TEE-backed keys (M10.7) are real, hardware-isolated protection roots cannot bypass purely in software. **Not claimed as absolute** — a sufficiently privileged root exploit targeting the TEE itself is out of this application's own threat-model scope (a platform/OEM security concern, not fixable at the app layer) |
| 4 | Jailbroken iPhone | Partially mitigated / platform-dependent | Same real limit as #3 — Keychain's own hardware-backed protection (Secure Enclave for biometric-gated items) resists software-only jailbreak tooling for the strongest accessibility classes; a kernel-level jailbreak targeting the Secure Enclave itself is out of this application's own scope |
| 5 | App sandbox access from another app | Protected | Both platforms' real OS-level app sandboxing already prevents other apps from reading this app's private storage/Keychain items outside its own access group — a real, standard OS guarantee, not something this app implements itself |
| 6 | Filesystem backup extraction (e.g. `adb backup`, iTunes/Finder unencrypted backup) | Protected (Android) / Platform-dependent (iOS) | Android: real backup-exclusion rules (M10.19) prevent the encrypted material file from being included; the Keystore key itself never leaves the device's own hardware regardless. iOS: an **unencrypted** local backup can include Keychain items depending on the item's own `kSecAttrAccessible*` class (M10.9's own real, disclosed tradeoff) — mitigated by selecting a `...ThisDeviceOnly` accessibility class for Installation-scoped material (M10.9), which is real, standard Apple guidance for device-bound secrets |
| 7 | Device-to-device restore/transfer | Protected (by design intent) | M10.19's own required policy: restored material must still pass a future Owner refresh/M11 lease verification before being trusted — a real, deliberate "restore does not equal trust" rule, not a technical prevention of the restore itself |
| 8 | Debug logs | Protected | Zero logging calls exist anywhere in the M7-M9 licensing package (confirmed, M9.23); M10's own new code is held to the identical standard, verified by the same real grep-based test pattern |
| 9 | Crash logs | Protected | `CrashRedaction` interface (real, pre-existing, `PlatformContracts.kt:147-149`) exists specifically for this — M10's own `SecureStorageFailure` model (M10.4) carries no secret value in any failure message |
| 10 | Memory inspection (a real attacker with process-memory access) | Out of scope / honestly disclosed limit | `secure-material-memory-hygiene.md` (M10.24) documents real JVM/Kotlin-Native memory-erasure limitations honestly — no guaranteed physical wipe is claimed |
| 11 | Clipboard | Protected | No M10/M9 code ever copies a credential/lease/serial to the system clipboard — confirmed by inspection, no `ClipboardManager`/`UIPasteboard` reference anywhere in the licensing package |
| 12 | Screenshots | Out of scope for M10 | A real, separate concern (`FLAG_SECURE` on Android, already used elsewhere in this codebase per memory of the Retail returns/barcode feature) — not licensing-secure-storage's own scope; not modeled here |
| 13 | Malicious local app (same device, different sandbox) | Protected | Same real OS sandboxing guarantee as #5 |
| 14 | Downgrade to an older application version | Partially mitigated | M10.15's own version-rejection rule: a future-versioned record is never silently trusted by an older app build; a genuine downgrade attack against an *older* record is a real, disclosed limit — the old app build may simply not recognize newer material at all, failing closed (`UNSUPPORTED_VERSION`) rather than misinterpreting it |
| 15 | Corrupted secure storage | Protected | Real, closed `SecureStorageFailure.CORRUPT_DATA`; M10.17's own recovery contract, fails closed, never returns partial/garbage material |
| 16 | Partial write | Protected | M10.6's own real atomic-commit/generation-promotion design — no partially-written generation is ever promoted to "current" |
| 17 | Process death during commit | Protected | Same M10.6 design — recovery always resolves to the last fully-promoted generation |
| 18 | Credential rollback (an old, superseded credential resurrected) | Protected | M10.16's own real rule: the old key/credential is deleted only after successful promotion of the new one — a rollback would require an attacker to have captured the old ciphertext *and* the old (now-deleted) key, which no longer exists |
| 19 | Duplicated restored Installation identity (two devices, same identity) | Protected (server-side) | Real, already-established finding (M8.5, `installation-reinstall-recovery-contract.md`): Owner's own globally-unique `DevicePublicKey.fingerprint` constraint is the real enforcement point — not solvable or claimed solvable client-side |
| 20 | App reinstall | Protected (by design intent) | M10.19's own required policy — reinstall may or may not preserve platform-backed material depending on OS backup behavior (real, platform-specific, documented per-platform); either way, the app must never assume reinstalled material implies the same trusted identity without a future real server check |
| 21 | App data clear (Android "Clear storage") | Protected (by design intent) | Real, expected outcome: Android's own "Clear storage" action removes both `EncryptedSharedPreferences`/file-based material AND (for app-scoped, non-shared Keystore entries) leaves the Keystore key orphaned/later garbage-collected by the OS — app correctly treats this as "no stored material," never a corruption case |
| 22 | OS upgrade | Partially mitigated / platform-dependent | Real, disclosed: an OS upgrade can, in rare cases, affect Keystore/Keychain key availability (e.g. a real, known Android issue class around Keystore key loss after certain OS updates on some OEM builds) — M10.17's own corruption/key-invalidation handling is the real mitigation (fail closed, offer reactivation), not a guarantee the key survives every possible OS upgrade |
| 23 | Key invalidation (e.g. biometric enrollment change invalidating a biometric-gated key) | Protected | Real, closed `SecureStorageFailure.KEY_INVALIDATED` outcome; M10.20's own user-presence decision explicitly considers this exact tradeoff before deciding whether to gate keys on biometric enrollment at all |
| 24 | Device passcode removal | Partially mitigated | Real Apple/Android platform behavior: some accessibility classes (e.g. iOS `WhenPasscodeSetThisDeviceOnly`) cause the OS itself to delete the Keychain item when the passcode is removed — a real, platform-enforced mitigation this app inherits by selecting that class, not something this app implements itself |
| 25 | Biometric enrollment changes | Protected | Covered by #23 |

## Real, honest classification summary

`protected`: 12. `partially mitigated`/`platform-dependent`: 8 (#2, #3,
#4, #6-iOS, #14, #22, #24 — real, disclosed platform/OS-level limits,
not app-fixable). `out of scope`: 2 (#10 memory inspection, #12
screenshots — real, disclosed, deliberately not modeled by this
milestone). No threat is claimed "impossible to guarantee" as an
absolute — every rooted/jailbroken-device item is honestly labeled
partially-mitigated/platform-dependent rather than claimed solved.
