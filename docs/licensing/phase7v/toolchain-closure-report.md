# Phase 7V — Toolchain Closure Report

## Inno Setup

- Already installed via winget (`JRSoftware.InnoSetup`, v6.7.3) from Wave 1B — `winget install`
  reported "no available upgrade found" rather than performing a fresh install.
- Actual binary location: `C:\Users\Dell\AppData\Local\Programs\Inno Setup 6\ISCC.exe` (user-scope
  install — not `C:\Program Files (x86)\Inno Setup 6\`, which is why the initial standard-path
  probe in Part A missed it).
- Verified runnable: `ISCC.exe /?` prints the compiler usage banner and version/copyright header.
- Discovery policy adopted for the Part E build step (not hardcoded to this one machine's path):
  1. `ISCC_PATH` environment variable, if set, used verbatim.
  2. `ISCC.exe` on `PATH`.
  3. Standard install locations, in order: `%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe`,
     `C:\Program Files (x86)\Inno Setup 6\ISCC.exe`, `C:\Program Files\Inno Setup 6\ISCC.exe`.
  4. If none found, fail with an actionable message naming all three fallback locations and the
     override variable — never silently skip the installer build.

## PostgreSQL client tools (`pg_dump` / `pg_restore`)

- Root cause of the 5 baseline Owner test failures: `owner/app/system/backup.py::_pg_bin()`
  returned the bare string `"pg_dump"` / `"pg_restore"` and relied entirely on the process's
  `PATH`. This machine has the PostgreSQL 17 server installed (`C:\Program
  Files\PostgreSQL\17\bin\pg_dump.exe`, version 17.10, exactly matching the running server) but its
  `bin` directory is not on `PATH`.
- Fix (`owner/app/system/backup.py`): `_pg_bin()` now resolves in order:
  1. `OWNER_PG_BIN_DIR` env var, if set — used verbatim, raises `BackupError` naming the missing
     tool and the misconfigured directory if not found there (fails loud, not silently falls
     through to PATH).
  2. `shutil.which(tool)` — PATH-based discovery, unchanged behavior for machines that do have it
     on PATH.
  3. Windows-only fallback: glob `C:/Program Files/PostgreSQL/*/bin`, newest version first (handles
     multi-version machines), picks the first directory containing the tool.
  4. If nothing matches, raises `BackupError` with an actionable message (name the tool, suggest
     installing matching client tools or setting `OWNER_PG_BIN_DIR`) instead of subprocess's opaque
     `[WinError 2] The system cannot find the file specified`.
- The credential-safety property from Phase 5 is unchanged: the resolved path is only ever used as
  `argv[0]` of the subprocess call; the password still travels exclusively via the `PGPASSWORD`
  environment variable of that one subprocess, never in argv or the resolved path itself.
- No test was weakened, skipped, or given a fake subprocess to make it pass — `tests/test_backup_
  restore.py` now exercises the real `pg_dump.exe`/`pg_restore.exe` binaries end-to-end (real dump
  file written, real checksum, real restore, real tampered-checksum rejection, real pre-restore
  safety backup). Result: 7/7 passing (previously 2/7, see `owner-regression-closure.md`).

## Android toolchain

- Android SDK present at `%LOCALAPPDATA%\Android\Sdk` (platform-tools, matches the Phase 4/Wave 1A
  setup recorded in prior memory).
- `adb.exe` present at `%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe`, version 1.0.41
  (37.0.0-14910828). Not on PATH by default in this shell — invoked by full path.
- `adb devices -l` at baseline: **no devices attached**. See `environment-readiness-report.md` and
  Part J for the physical-device gate this implies.
- Gradle: no global install; both `android/aura-clinic` and `android/aura-retail` pin Gradle 8.9
  via their own wrapper (`gradle-wrapper.properties`) — this is the project's existing supported
  invocation path (`gradlew.bat`), not a gap.
- Production keystores confirmed present outside Git at `C:\Users\Dell\AuraSigningKeys\` (
  `clinic-release.keystore`, `retail-release.keystore`, plus sibling `.pass` files). Contents,
  passwords, and aliases were not printed or logged. `android/aura-clinic/keystore.properties` and
  `android/aura-retail/keystore.properties` exist locally (gitignored) and are read by each
  product's existing `app/build.gradle` signing-config block. No new keystore was generated; no
  existing keystore was modified.

## Outcome

All toolchain gaps identified in the Part A baseline are closed except the physical Android
device, which is an external dependency outside toolchain configuration (see Part J).
