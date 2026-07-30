# Phase 8V-P3 — Baseline

## Git state verified before any work (2026-07-30)

```
git status --short                                                    -> clean
git rev-parse HEAD                                                    -> 4a87894aef3357d9f311d426c1e792722b60b26c
git branch --show-current                                             -> master
git rev-parse aura-owner-commercial-ops-phase8-conditional-complete   -> 7150564af95bd75cd475df57a6b42ae9ad4b3fb5
git log --oneline --decorate 7150564..HEAD                            -> 11 commits, ending at 4a87894
```

HEAD matches the value this phase's governing brief states exactly. The conditional tag is unmoved.
The original `AuraEnterprise` repository was not opened or modified this session.

## Prior evidence read before any new work

`docs/owner/phase8vp2/PHASE8VP2-FINAL-ANDROID-CLOSURE-HANDOVER.md`,
`phase8-final-release-decision.md`, `android-physical-gate-matrix.md`, `android-rc3-build-report.md`,
`android-certificate-continuity.md`, `scenario7-final-evidence.md`, `environment-preflight-report.md`,
`final-regression-report.md`, `final-android-artifact-evidence.md` — all read in full.

## ADB / physical device check (real, done first, gates everything else)

```
$ adb kill-server && adb start-server && adb devices -l
* daemon started successfully
List of devices attached
```

Empty list. See `device-readiness.md`. Per this phase's own Part B: stop before any device-dependent
work, report the gate, do not fake, do not tag, do not begin Phase 9.

## What was still done for real this session despite no device

Everything in Part A/C/D of the governing brief that does not require a device:

1. Real preflight reconfirmation against the live `aura_owner_dev` database (`ok: true`).
2. Re-hashed the existing rc.3 Android artifacts against the recorded SHA-256 evidence — unchanged,
   confirming no drift since Phase 8V-P2, no rebuild needed.
3. **A genuinely new finding**: the currently-built rc.3 artifacts have an **empty**
   `OWNER_LICENSING_BASE_URL` baked in (`BuildConfig.OWNER_LICENSING_BASE_URL = ""`) — the
   deliberate, documented fail-safe default for an unconfigured build (see
   `android/aura-clinic/app/build.gradle`'s own comment: "Empty by default... never a hidden fallback
   URL"). This means the artifacts currently in `dist/android/` will report licensing
   `NOT_CONFIGURED` if installed as-is. See `artifact-verification.md` for the full finding and the
   exact rebuild command the next device session needs.
4. Full owner (394/394) and commercial_runtime (219/219) suites re-run for real at this exact HEAD,
   confirming no drift.

## What is blocked and was not attempted

Everything from Part E onward that requires a connected device: installation, all seven physical
scenarios, traffic capture, Logcat review, on-device data preservation and backend-enforcement
checks, and the final tag. See `physical-validation-matrix.md`.
