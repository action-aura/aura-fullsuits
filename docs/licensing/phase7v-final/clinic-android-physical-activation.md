# Phase 7V-F — Clinic Android Physical Activation (Part G)

## Completed before device disconnect

- Existing patient/invoice records confirmed readable via the real on-device API before any
  licensing configuration was present (`GET /api/sub/clinic/patients`/`invoices` both returned the
  full synthetic dataset created in `clinic-android-physical-upgrade.md`).
- A later APK rebuild round added `-PownerLicensingBaseUrl=http://127.0.0.1:19001/api/licensing/v1`
  and the real trust anchor, specifically to enable on-device activation against the real
  production-like Owner (reachable from the device via `adb reverse tcp:19001 tcp:19001`).

## Status: on-device activation itself NOT completed

The device disconnected before the rebuilt, Owner-URL-configured APK could be reinstalled and an
activation attempt driven on the physical hardware. The activate→check-in→restart→offline→
warning→restricted→authority-boundary→Logcat sequence that WAS completed live is documented in
`windows-live-restricted-mode-closure.md` — proving the identical shared licensing core
(`commercial_runtime/licensing_contracts/`) that Android's embedded Python also runs, including the
real P0 trusted-time fix found and fixed this session, but on Windows, not on the physical Android
device itself.

## What this does and does not prove

Does prove: the exact same Python code that would run inside Android's Chaquopy interpreter for
activation, assertion verification, and offline-state evaluation is correct and was live-tested
end-to-end against a real Owner. Does not prove: the Android-specific integration layer (Kotlin's
`OwnerClient.kt` → `/_internal/sync-*` hand-off → embedded Python re-verification) actually works
correctly under real Android process/threading conditions, real AndroidKeystore-wrapped device-key
behavior, or real on-device network stack behavior — none of that is exercised by a Windows test,
however similar the underlying policy-evaluation code is.

## Verdict

**NOT VERIFIED** (physical, Android-specific integration layer). Shared licensing-core correctness:
**PASS** (proven live, on Windows, same code).
