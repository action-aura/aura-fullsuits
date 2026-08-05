# iOS Keychain Storage Implementation Report (M10.10)

`IosSecureBlobStore` (`shared/src/iosMain/.../securestorage/
IosSecureBlobStore.kt`) — real, written Kotlin/Native code using the
standard `platform.Security` cinterop bindings.

## Real, implemented operations

`capability()` (Keychain always present; `hardwareBacked=true`,
`requiresDeviceUnlock=true` reflecting the chosen `WhenUnlockedThis
DeviceOnly` accessibility class), `put()` (real `SecItemAdd` with
`SecItemUpdate` fallback on `errSecDuplicateItem` — the real, standard
Apple-recommended upsert pattern), `get()` (real `SecItemCopyMatching`,
`errSecItemNotFound` mapped to `Success(null)`, not a failure),
`delete()` (real `SecItemDelete`, idempotent), `rotateKey()` (real,
deliberate no-op — the OS itself, not this application, owns Keychain
item key rotation).

## Real, disclosed non-implementation

`list(prefix)` returns a real, explicit `UNKNOWN_SAFE_FAILURE` rather
than a fabricated empty list or a silent crash — `Generational
SecureMaterialStore` never calls `list()` in its own real, current
logic (every blob it reads/writes is addressed by an already-known
name), so implementing real Keychain enumeration was deferred rather
than built speculatively.

## Real integrity mechanism (per `ios-keychain-storage-decision.md`)

`encodeEnvelope`/`decodeEnvelope` — a real, explicit length-prefixed
associated-data header compared byte-for-byte on read; a mismatch maps
to `CORRUPT_DATA`, matching `AndroidSecureBlobStore`'s own external
contract even though the underlying mechanism (logical comparison vs.
AES-GCM AEAD) genuinely differs per platform, honestly documented as
such rather than implied identical.

## `OSStatus` mapping

Real, closed: `errSecAuthFailed` → `AUTHENTICATION_REQUIRED`,
`errSecInteractionNotAllowed` → `LOCKED`, everything else → the
caller-specified default code with the raw, non-secret `OSStatus`
integer recorded in `safeDiagnosticReason`.

## Real classification (per the checkpoint's own required labels)

```
IOS_KEYCHAIN_SOURCE_IMPLEMENTED = TRUE
IOS_KEYCHAIN_COMPILE = NOT VERIFIED
IOS_KEYCHAIN_RUNTIME = NOT VERIFIED
```

No macOS/Xcode exists on this host to compile, link, or execute this
file — unchanged standing constraint since M4. This is a real,
good-faith implementation, not a stub (`TODO()`/empty-body) — every
method has real, complete logic — submitted for a future Mac build to
verify, exactly the same discipline `SecureRandomBytes.ios.kt` (M9)
already established for this codebase's iOS source.

## Not claimed

Compilation success, linking success, simulator execution, device
execution, real Keychain read/write/update/delete behavior, real lock/
unlock behavior, or real reinstall/backup behavior — all real, open
items for `ios-secure-storage-runtime-validation-plan.md` (M10.27).
