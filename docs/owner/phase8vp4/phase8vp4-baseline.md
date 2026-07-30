# Phase 8V-P4 — Baseline

## Hard entry gate: PASSED (real, checked first, before any other work)

```
$ adb kill-server && adb start-server && adb devices -l
* daemon started successfully
List of devices attached
1122070476060894       device product:X6528-OP model:Infinix_X6528 device:Infinix-X6528 transport_id:1
```

Status `device` (authorized, usable) -- first time across four sessions (Phase 8V-P, 8V-P2, 8V-P3,
8V-P4) that a physical Android device has been present. Serial `1122070476060894` matches the
previously-used device referenced (as a preference, never an assumption) in this phase's own
governing brief.

## Git baseline (verified after the device gate passed, per this phase's own ordering)

```
git status --short                                                    -> clean
git rev-parse HEAD                                                    -> ab774e5f50c9f27ce646360385a90b8029fc8157
git branch --show-current                                             -> master
git rev-parse aura-owner-commercial-ops-phase8-conditional-complete   -> 7150564af95bd75cd475df57a6b42ae9ad4b3fb5
git rev-parse aura-commercial-licensing-operations-phase8-complete    -> fatal: unknown revision (does not exist, correct)
git log --oneline --decorate 7150564..HEAD                            -> 12 commits, ending at ab774e5
```

HEAD matches this phase's stated expected value exactly. Conditional tag unmoved. Final tag does not
exist. Original `AuraEnterprise` repository not opened this session.

## Prior evidence read before new work

`docs/owner/phase8vp3/PHASE8VP3-PHYSICAL-ANDROID-CLOSURE-HANDOVER.md`,
`phase8-final-decision.md`, `device-readiness.md`, `artifact-verification.md`,
`docs/owner/phase8vp2/PHASE8VP2-FINAL-ANDROID-CLOSURE-HANDOVER.md`, `scenario7-final-evidence.md`,
`environment-preflight-report.md`, `android-certificate-continuity.md`. Key facts carried forward:
exact rebuild command needs `-PownerLicensingBaseUrl=<full path including /api/licensing/v1>`
(the client library treats the value as complete, never appends the suffix itself); historical full
certificate SHA-256 fingerprints (Clinic `35508048cee7776ca94a45a9f04c6a870edf98729429482a8ad44e89dd0bbbf2`,
Retail `cae6b18450c14a71eba47545e5b3ba52089eef0397d5881f4c1dc7d5a4b7d32d`); synthetic credentials
(`phase8vp-admin@example.com` / `phase8vp-approver@example.com`, both `Sup3r-Str0ng-Pass!`, approver
TOTP secret `UIQNTBZ2A45E2DKTMSMTKGVQ566ME64P`, from `docs/owner/phase8vp/PHASE8VP-PHYSICAL-COMMERCIAL-CLOSURE-HANDOVER.md`
section 6-7); Owner dev server startup command (same section 6.3).
