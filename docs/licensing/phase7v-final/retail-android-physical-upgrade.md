# Phase 7V-F — Retail Android Physical Signed Upgrade (Part D/F)

## Status: NOT COMPLETED — device disconnected before this part

The physical device also had Retail's real production-signed rc.1 APK installed, and its signer
fingerprint was verified the same way as Clinic's (real `adb pull` + `apksigner verify`):

```
Signer #1 certificate SHA-256 digest: cae6b18450c14a71eba47545e5b3ba52089eef0397d5881f4c1dc7d5a4b7d32d
```

Matches the recorded rc.1 fingerprint (`CA:E6:B1:...:B7:D3:2D`) exactly, and matches this session's
rc.2 build exactly — real fingerprint continuity confirmed for Retail too.

**Beyond fingerprint verification, no further Retail Android physical work was completed.** The
device disconnected (confirmed genuinely absent via `adb kill-server && adb start-server && adb
devices -l`) before Retail's `pm clear`/re-onboard/synthetic-data/upgrade-install sequence could be
started, and did not reconnect for the remainder of the session despite repeated checks.

## What was NOT verified

- Retail Android synthetic data creation.
- Retail Android real backup.
- Retail Android signed rc.1→rc.2 upgrade install.
- Retail Android post-upgrade data preservation.
- Retail Android physical activation, check-in, offline/warning/restricted, suspend/reactivate/
  deactivate.

## What stands in as partial evidence

- Real signer-continuity proof (above) — the same underlying Android package-installer signature
  check that gates a real upgrade install, verified via direct fingerprint comparison rather than
  the full install-and-observe sequence.
- Retail's rc.2 build was verified via Windows (identical shared `commercial_runtime` code, same
  trusted-time fix, same activation/offline/RESTRICTED lifecycle proven live — see
  `windows-live-restricted-mode-closure.md`).
- Retail's `cryptography` dependency fix and `assembleRelease` build both succeeded for this
  specific APK (confirmed green build, not just Clinic's).

## Verdict

**NOT VERIFIED** (physical upgrade-and-lifecycle sequence). Signer continuity: **PASS** (real,
on-device). This is a genuine, honestly-reported gap — not fabricated, not assumed complete.
