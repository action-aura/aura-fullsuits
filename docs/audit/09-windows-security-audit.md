# Windows Security Audit

Evidence source: this audit's own checks (`08`) + real packaged-exe verification
already performed and documented in Phase 2B/3 (`docs/build/retail-windows-build-report.md`,
`docs/build/clinic-windows-build-report.md`), re-confirmed applicable since
`products/`/`commercial_runtime` are unmodified since those builds (`01`).

## Writable directories — PROVEN

`launcher_retail.py`/`launcher_clinic.py` write all runtime data (database,
logs, per-install secret key) under `%LOCALAPPDATA%\AuraRetail`/`AuraClinic`
(`_app_data = os.environ.get('AURA_APP_DATA') or str(Path(LOCALAPPDATA)/'AuraRetail')`),
**not** inside the executable's own install directory. The Phase 2B/3 packaged
smoke tests explicitly confirmed "no writes were attempted inside the install
directory itself" against the real running `.exe`. This is the correct pattern
— avoids the classic "app tries to write next to itself in `Program Files` and
fails/needs admin" class of Windows packaging defect. **No finding.**

## Executable signing — PROVEN GAP

Neither `AuraRetail.exe` nor `AuraClinic.exe` is code-signed (explicitly stated
in both Windows build reports: "No code-signing certificate applied — none
available in this environment"). An unsigned Windows executable triggers
SmartScreen/Defender warnings on first run for real customers — a real,
already-documented commercial-readiness gap (not new to this audit), tracked
here as `AUDIT-###` in the master registry as a release-blocker for Gate 3
(paid SMB customers), not Gate 1/2.

## Installer — PROVEN GAP

No Inno Setup (or equivalent) installer wrapper exists for either product — the
build reports confirm "this validates the raw PyInstaller output only." A real
customer receiving a raw `onedir` folder (not a single signed installer .exe)
is a materially worse first impression and a support burden (no Start Menu
entry, no uninstall entry, no update mechanism hook).

## Single-instance / port conflict — PROVEN

`launcher_retail.py::_acquire_single_instance()` uses a named Windows mutex
(`Global\AuraRetail_<app-data-dir-name>`) to detect and block a second
instance, showing a native `MessageBoxW` if already running. `_find_free_port()`
scans `DEFAULT_PORT..DEFAULT_PORT+20` rather than hard-failing on the first
occupied port. **Both correctly implemented**, confirmed by direct source read
in a prior phase and re-confirmed present in current source (unmodified since).

## Antivirus false-positive risk — UNVERIFIED

Not tested against a real antivirus product in this pass (no such environment
available) — PyInstaller-built, unsigned executables are a commonly-flagged
pattern industry-wide; this is a known risk class rather than a confirmed
false-positive in this specific build. Flagged as UNVERIFIED, not asserted.

## Crash recovery / temp files / DLL search — PROVEN, no findings

No DLL-search-order hijack surface was found (PyInstaller onedir bundles its
own dependencies in `_internal/`, not relying on ambient PATH lookups for
anything beyond standard Windows system DLLs). Log files write to the same
app-data directory as the database, not to a world-writable temp location.

## Summary

| Item | Status |
|---|---|
| Writable directory correctness | PASS |
| Single-instance guard | PASS |
| Port-conflict handling | PASS |
| Debug/console window suppressed | PASS (`console=False` in both specs) |
| Code signing | **FAIL** — none, by design, this phase |
| Installer | **FAIL** — raw onedir only, no wrapper |
| Antivirus compatibility | UNVERIFIED |
| Update mechanism | NOT PRESENT (out of scope for this audit and for Phase 4/prior phases — explicitly deferred) |
