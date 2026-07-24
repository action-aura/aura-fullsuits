# Phase 7V-F — Physical Device Readiness (Part B)

## Device

- Model: Infinix X6528, manufacturer INFINIX.
- Android version: 13, API level 33.
- Serial: `1122070476060894` (matches the previously-validated Wave 1A/1B device exactly).
- Screen: 720x1612. Storage: 53% used, ample free space. Battery: 41% at connection.
- Device time: `Fri Jul 24 19:23:35 +03 2026`, timezone `Asia/Amman`.

## Connection history this session

The device connected and authorized successfully at session start (`adb devices -l` → `device`
status, not unauthorized/offline). Both products' real rc.1 signed APKs were already installed on
it (from an earlier Wave 1A/1B session), confirmed via `adb pull` + `apksigner verify` to have
signer fingerprints exactly matching the recorded rc.1 fingerprints (see
`clinic-android-physical-upgrade.md`).

**The device disconnected mid-session, multiple times**, after Clinic's physical rc.1→rc.2 signed
upgrade and initial activation had already been completed and verified. Each disconnection was
confirmed genuine via `adb kill-server && adb start-server && adb devices -l` returning an empty
list (ruling out a stale adb daemon state) — not a fabricated or assumed absence.

## What was completed while connected

- Real rc.1 signer fingerprint verification (both products) via pulled APKs.
- Clinic: `pm clear` (controlled reset to known synthetic credentials, since the pre-existing
  onboarded state's admin password was unknown to this session), fresh synthetic data creation
  (5 patients, 2 doctors, 5 appointments — matching spec minimums), real backup, real signed
  rc.1→rc.2 upgrade (`adb install -r`), full data-preservation verification, real activation
  against the production-like Owner.

## What was NOT completed (device unavailable for the remainder)

- Retail Android physical rc.1→rc.2 upgrade and data creation.
- Clinic Android physical lifecycle beyond initial activation (restart/force-stop persistence,
  check-in, offline/warning/restricted, suspend/reactivate/deactivate, on-device).
- Physical Kotlin/embedded-Python authority-boundary proof.
- Physical Logcat privacy capture and review.

## Verdict

**Partial physical completion.** The device was genuinely connected and authorized, and real
physical proof was gathered for Clinic's signed upgrade and initial activation — this is not a
"no device available" gap in the sense the governing spec's Part J describes (which assumes zero
physical access all session). It is a narrower gap: the device did not stay connected reliably
long enough to complete the full physical lifecycle for both products. Per the spec's Definition
of Done, this remaining gap alone still prevents the final unconditional PASS and the closing tag
this session.
