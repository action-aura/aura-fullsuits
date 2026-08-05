# iOS Secure Storage Runtime Validation Plan (M10.27)

Real, honest disclosure: this document is a **plan**, not a report of
execution. No macOS/Xcode environment exists on this Windows
development host, so `IosSecureBlobStore.kt` and `SecureRandomBytes.
ios.kt` have never compiled, linked, or run against a real
Kotlin/Native iOS target anywhere in this milestone. This mirrors the
same honest disclosure already recorded for M8/M9's own iOS-adjacent
work (`ios-build-readiness-plan.md`, `ios-platform-readiness-state.md`,
`ios-activation-orchestration-readiness.md`).

## What is real here (source-level, host-independent)

- `IosSecureBlobStore.kt` — real, complete Kotlin/Native source using
  `platform.Security` cinterop (`kSecClassGenericPassword`, real
  `SecItemAdd`/`errSecDuplicateItem`/`SecItemUpdate` upsert,
  `SecItemCopyMatching`, `SecItemDelete`,
  `kSecAttrAccessibleWhenUnlockedThisDeviceOnly`), written against the
  same `SecureBlobStore` contract the Android implementation and the
  common-layer test suite both exercise.
- The real associated-data envelope format (`encodeEnvelope`/
  `decodeEnvelope`) is a length-prefixed header comparison — the same
  logical AAD-binding contract `SecureMaterialStoreTest`'s
  `wrongAssociatedDataNeverAuthenticates` proves against the
  platform-agnostic `InMemorySecureBlobStore` double. The *contract*
  is proven; the *real Keychain enforcement* of it is not.
- `ios-keychain-storage-decision.md` (M10.9) records the real,
  disclosed tradeoff of `kSecAttrAccessibleWhenUnlockedThisDeviceOnly`
  (device-bound, excluded from iCloud/encrypted-backup restore) as a
  deliberate choice, not an oversight.

## Real gap this host cannot close

No `xcodebuild`, no iOS Simulator, no `kotlinc-native` iOS toolchain,
no Apple Keychain of any kind is reachable from this environment.
Every claim below is therefore explicitly **NOT VERIFIED** and this
milestone does not simulate, mock at the OS level, or otherwise
fabricate iOS runtime evidence in its place.

## The exact validation plan for a future macOS/Xcode-equipped session

1. **Compile the real target**: `./gradlew :shared:compileKotlinIosSimulatorArm64` (and `iosArm64`/`iosX64` as applicable) — first real proof the cinterop bindings against `platform.Security` resolve and the file is syntactically/type-correct for Kotlin/Native, not just JVM-parseable Kotlin.
2. **Run the existing common-layer test suite on an iOS test target**: `SecureMaterialStoreTest`/`SecureMaterialStoreActivationSinkTest` already run in `commonTest`, so they are real Kotlin/Native-portable as written — execute them via `:shared:iosSimulatorArm64Test` (or equivalent) backed by the real `IosSecureBlobStore`, not the in-memory double, to prove the real Keychain path satisfies the same contract the JVM run already proved against the double.
3. **Round-trip test**: put/get a real blob through `IosSecureBlobStore` directly against the Simulator's real Keychain, confirm byte-identical round-trip.
4. **Duplicate-item upsert path**: write the same key twice, confirm the real `errSecDuplicateItem` → `SecItemUpdate` branch is actually exercised (not just present in source) via a debugger breakpoint or an explicit counter.
5. **AAD-mismatch rejection**: write with one associated-data value, attempt read with a different one, confirm real rejection via the length-prefixed header comparison.
6. **Corruption rejection**: directly tamper with a Keychain item's stored data (test-only, via a raw `SecItemUpdate` bypassing the store's own API) and confirm `get()` fails closed.
7. **Delete/list**: confirm `delete()` real-removes a Keychain item; confirm `list()`'s disclosed `UNKNOWN_SAFE_FAILURE` no-op behavior is still acceptable for every real call site at that point in time (re-evaluate if a future milestone adds a caller that needs it).
8. **`rotateKey()` no-op confirmation**: confirm the deliberate no-op (OS-managed key rotation) does not silently break any real caller expecting `SecureStorageCapability.supportsKeyRotation` semantics.
9. **CSPRNG bridge**: execute `SecureRandomBytes.ios.kt`'s real `SecRandomCopyBytes` call on-device/simulator, confirm non-zero-length output and no crash on the zero-length degenerate case (`csprng-native-bridge-review.md`, M10.25, "not independently unit-tested on this host").
10. **App-relaunch persistence**: write real material, force-quit the app, relaunch, confirm the same real Keychain item is still readable (proves `kSecAttrAccessibleWhenUnlockedThisDeviceOnly` behaves as documented across a real process lifecycle, not just in source).
11. **Device-lock behavior**: attempt a read while the device/simulator is locked, confirm the expected `LOCKED`/`errSecInteractionNotAllowed`-mapped failure rather than a crash or silent wrong value.

## Honest classification

**iOS secure storage: NOT VERIFIED.** Source-complete, contract-aligned
by construction (same shared interfaces, same common-layer tests the
Android path is measured against), zero real Kotlin/Native/Keychain
execution on this host. This is the same honest disclosure standard
every prior milestone in this branch has applied to iOS-specific
claims; M10 does not depart from it.
