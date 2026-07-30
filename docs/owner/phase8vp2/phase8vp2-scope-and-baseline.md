# Phase 8V-P2 — Scope and Baseline

## What this phase is

The final narrow continuation of Phase 8: resolve the one remaining real defect (Scenario 7's
device-allowance-to-device-limit sync gap), harden the environment against the two real gaps found
in Phase 8V-P (stale trust anchor, under-seeded permissions) with a deterministic preflight command,
build final signed Android rc.3 artifacts, and attempt physical Android validation. Not a redesign,
not Phase 9.

## Baseline verified before any change (2026-07-30)

```
git status --short         -> clean except one doc file from the prior turn's handover rewrite
git rev-parse HEAD          -> 9eace9e3abe40982d0b48f4c58e7f2a00dd10744
git branch --show-current   -> master
git rev-parse aura-owner-commercial-ops-phase8-conditional-complete -> 7150564af95bd75cd475df57a6b42ae9ad4b3fb5
```

HEAD matches the Phase 8V-P session's final commit exactly. The conditional tag is unmoved. The
original `AuraEnterprise` repository was not opened or modified at any point this session.

## ADB / physical device check (real, not assumed)

```
$ adb kill-server && adb start-server && adb devices -l
* daemon started successfully
List of devices attached
```

Empty list — genuinely no physical Android device connected this session. ADB itself is functional
(daemon starts, responds normally). Per this phase's own rule, this blocks Part D onward (physical
device readiness, all physical scenario execution, the final tag) — disclosed here before any other
work began, not discovered as a late excuse. See `physical-android-readiness.md`.

## What was in scope and completed this session without a device

1. Scenario 7 root cause, fix, and regression evidence (`scenario7-*.md`).
2. A deterministic `flask commercial preflight` command covering both real environment gaps found in
   Phase 8V-P (`environment-preflight-report.md`) — which itself found and fixed a **third**, related,
   previously-undetected environment drift (see that report).
3. Final signed Android rc.3 release builds for both products, including real certificate-continuity
   verification against the historical production signing identity (`android-rc3-build-report.md`,
   `android-certificate-continuity.md`) — genuinely achievable without a physical device (Part F/G
   don't require one; Part D onward does).
4. Full final regression: Owner, commercial_runtime, both Android modules' unit/lint suites.

## What remains genuinely blocked

Physical installation, physical scenario execution (Parts I through W), real Android-to-Owner
traffic capture from a real device, Logcat privacy review, and the final unconditional Phase 8 tag.
See `phase8-final-release-decision.md` for the complete accounting.
