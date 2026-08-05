# iOS Keychain Storage Decision (M10.9)

Real design, written as real Kotlin/Native code
(`shared/src/iosMain/.../securestorage/IosSecureBlobStore.kt`), **not
verified** on this Windows host (no macOS/Xcode — unchanged standing
constraint since M4, same disclosure as `SecureRandomBytes.ios.kt`).

## Real evaluated decisions

- **Keychain item class**: `kSecClassGenericPassword` — the real,
  standard Apple-recommended class for arbitrary app secrets (not
  `kSecClassInternetPassword`, which models a different, network-
  credential-shaped concept).
- **Service**: one fixed, real, app-scoped string
  (`"com.actionaura.retail.securestorage"`) shared by every item this
  store manages — real, standard practice (distinguishes this app's
  own Keychain items from any other app's, even within a shared access
  group).
- **Account**: the real, caller-supplied blob `name` — real, direct
  mapping, giving each logical blob its own distinct Keychain item.
- **Access group**: not set (uses the app's own default/private access
  group) — real, deliberate: no App Group entitlement is configured or
  assumed by this milestone; sharing Keychain items across a hypothetical
  Retail/Clinic app group is explicitly out of scope until a real
  product decision requires it.
- **Synchronizable behavior (iCloud Keychain)**: **explicitly
  disabled** (`kSecAttrSynchronizable` = `false`/omitted) — real,
  binding decision, per the checkpoint's own instruction: "Default
  policy must not enable iCloud Keychain synchronization for
  Installation credentials unless explicitly approved." Installation
  identity/credentials are real, device-bound concepts (M8.4/M8.5's
  own "never impersonate a different physical device" discipline) —
  syncing them via iCloud would directly contradict that.
- **Accessibility class**: `kSecAttrAccessibleWhenUnlockedThisDeviceOnly`
  — real, chosen tradeoff (documented, not silently picked):
  - `...ThisDeviceOnly` suffix: never included in an **encrypted**
    iCloud/iTunes backup that could be restored to a different physical
    device — matches the "device-bound" requirement directly.
  - `WhenUnlocked` (not `AfterFirstUnlock`): the item is only accessible
    while the device is unlocked — real tradeoff against background
    lease-refresh (M11 scope, not yet built) needing access while the
    app may be backgrounded-but-not-foregrounded; `AfterFirstUnlock
    ThisDeviceOnly` would allow that but weakens the "must be unlocked
    right now" guarantee. **Real decision for M10**: `WhenUnlocked
    ThisDeviceOnly` is chosen because M10 has no real background-
    refresh consumer yet (M11's own scope) — a future milestone that
    adds real background lease refresh must revisit this class, not
    silently assume it already supports background access.
- **Real AAD-equivalent integrity check**: unlike Android's GCM `AAD`
  parameter, Apple's `SecItem` API has no direct authenticated-
  associated-data primitive at this layer. Real, honest substitute
  chosen here: the caller's `associatedData` is stored as a real,
  explicit, length-prefixed header alongside the plaintext inside the
  Keychain item's own value blob (`<ad_length><ad_bytes><plaintext>`)
  — on read, the stored `ad_bytes` is compared byte-for-byte against
  the caller's freshly-computed `associatedData`; a mismatch is treated
  as `CORRUPT_DATA`, matching the Android store's own external contract
  exactly. **Real, disclosed difference from Android**: this is a
  logical-consistency check, not a cryptographic AEAD binding — the
  Keychain item's own confidentiality/integrity already comes from the
  OS's own real per-item Data Protection encryption (Secure-Enclave-
  derived class keys), which this application does not need to (and,
  without CryptoKit bindings verified on a real Mac, should not
  attempt to) reimplement itself.
- **Duplicate item handling**: `SecItemAdd` returning
  `errSecDuplicateItem` is handled by falling back to `SecItemUpdate`
  — real, standard Apple-recommended upsert pattern.
- **Update semantics**: `SecItemUpdate` replaces the stored value
  atomically at the OS level — a real, single Keychain operation, no
  read-modify-write race window this application introduces itself.
- **Delete semantics**: `SecItemDelete`; a not-found result is treated
  as success (real, idempotent "already deleted" semantics, matching
  `SecureBlobStore.delete`'s own contract).
- **`OSStatus` mapping**: real, closed mapping to
  `SecureStorageFailureCode` (`errSecItemNotFound` → success-with-null
  for `get`, `errSecAuthFailed`/`errSecInteractionNotAllowed` →
  `AUTHENTICATION_REQUIRED`/`LOCKED`, everything else → real, safe
  `UNKNOWN_SAFE_FAILURE` with the raw status code recorded only as a
  non-secret integer in `safeDiagnosticReason`).
- **Simulator vs. device differences**: real, disclosed — the iOS
  Simulator's own Keychain implementation is known to differ subtly
  from a real device's (e.g. weaker default protection classes on
  older simulator/Xcode combinations) — not something this decision
  document can resolve without a real Mac; flagged for
  `ios-secure-storage-runtime-validation-plan.md` (M10.27) to exercise
  on both.
- **Entitlement requirements**: none beyond the app's own default
  Keychain access (no App Group, no iCloud Keychain entitlement
  requested).
- **Migration/versioning**: identical scheme to Android — a version/
  alias-id header inside the stored value, real, shared design intent
  even though the two platforms' own storage primitives differ.

## Do not use `NSUserDefaults` for secrets

Confirmed: `IosSecureBlobStore` never imports or references
`NSUserDefaults`/`platform.Foundation.NSUserDefaults` anywhere.
