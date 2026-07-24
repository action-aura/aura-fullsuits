# Phase 7V-F — Final Closure Baseline (Part A)

## Verified at session start

- Tag `aura-product-licensing-integration-phase7-complete` → commit `6ef6265` — confirmed unchanged.
- HEAD included `a6a19dd` (Phase 7V's closing commit) — confirmed.
- Branch `master`, working tree clean at session start.

## Toolchain

- ADB: 1.0.41 (37.0.0-14910828), at `%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe`.
- Inno Setup: 6.7.3, at `%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe`.
- PostgreSQL: 17.10 server, `pg_dump`/`pg_restore` 17.10 at `C:\Program Files\PostgreSQL\17\bin\`.
- Owner active signing-key ID at session start: reused from Phase 7V (rotated during this session
  when the production-like Owner environment was created — see
  `production-like-owner-validation-environment.md`).

## Baseline regression (before any Phase 7V-F change)

Re-ran and confirmed still green before starting: Owner suite and the 46-file combined product
runner, matching Phase 7V's closing state exactly (186 Owner / 585 product tests, 0 failures).

## Artifact paths confirmed present

- Windows rc.1 installers: `dist/installers/AuraClinic-Setup-1.0.0-rc.1.exe`,
  `AuraRetail-Setup-1.0.0-rc.1.exe`.
- Windows rc.2 installers (from Phase 7V): present, superseded by this session's rebuilds (see
  `final-artifact-reconfirmation.md`).
- Android rc.1: not present as files on disk this session either; verified live via a real
  `adb pull` of the actually-installed rc.1 APKs on the physical device and `apksigner verify`
  (see `clinic-android-physical-upgrade.md`) — a stronger proof than reading a recorded checksum.
- Android rc.2 (Phase 7V): present, superseded by this session's rebuilds.

See `remaining-gate-matrix.md` for the itemized gap list this session set out to close.
