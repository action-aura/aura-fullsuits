# Phase 7V — Android Clinic Physical Licensing Validation (Part L)

## Status: NOT VERIFIED — no physical device connected

Same gate as Part K (`android-physical-signed-upgrade-evidence.md`) — `adb devices -l` empty
throughout this session. None of the 27 numbered steps in the spec's Part L (launch, pre-activation
data read, activate, restart/force-stop/kill persistence, check-in, offline behavior, restricted-mode
data-access matrix, suspend/reactivate, deactivate, network/Logcat inspection) were performed on
real Android hardware.

## What the equivalent Windows lifecycle proved (same shared Python licensing core)

Every one of these lifecycle steps was proven live this session on Windows, against a real Owner
instance, using the same `commercial_runtime/licensing_contracts/` package Android's embedded
Python also runs (see `windows-rc1-to-rc2-installer-validation.md`): activate, restart persistence,
check-in, real Owner outage → `ACTIVE_OFFLINE`, live `WARNING`, live `GRACE_PERIOD`, deactivate. The
Android-specific parts this cannot stand in for are: Kotlin's own `OwnerClient.kt` HTTP layer, the
`/_internal/sync-*` localhost hand-off, AndroidKeystore-wrapped device-key behavior, and any
Android-specific UI/Logcat/privacy behavior — none of which were exercised on real hardware this
session.

## Unit-level coverage that was re-run and passed this session

`test_android_bridge_identity.py` (11/11), `test_internal_sync_routes.py` (6/6),
`test_assertion_verifier.py` (15/15), plus the full Android release build's `testReleaseUnitTest`
(passed for Clinic, see `android-rc2-signed-build-report.md`).

## Verdict

**NOT VERIFIED** (physical). Unit/source-level coverage for the shared logic: PASS. This gap is a
mandatory Definition-of-Done item — its absence withholds the final Phase 7V closing tag.
