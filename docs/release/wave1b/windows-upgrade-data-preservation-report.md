# Wave 1B — Windows Upgrade / Data Preservation Report

## Why this is safe by construction, not by a special case
Both installers' `[Files]` section writes exactly one thing: the frozen PyInstaller payload, into `{app}` (`%LOCALAPPDATA%\Programs\Action Aura\Aura Retail|Clinic`). Neither `.iss` script has any `[Files]` or `[Dirs]` entry referencing `%LOCALAPPDATA%\AuraRetail` / `%LOCALAPPDATA%\AuraClinic` (the actual data directories, resolved entirely inside the Python app itself via `launcher_{retail,clinic}.py`'s `AURA_APP_DATA` logic, unchanged this wave). An installer cannot destroy what it never touches — upgrade-safety here is an architectural property, not a behavior that had to be carefully coded and could regress.

## What was proven, not assumed
- **Upgrade** (Retail): real sale (`SALE-000001`, $100.00, real idempotency key) created under `1.0.0-rc.1`, then a genuinely different compiled installer (`1.0.0-rc.2`, same AppId) silently installed over the running install. Registry version updated correctly (proving Inno Setup recognized it as an upgrade of the same product, not a side-by-side install). Sale total, login, and admin account all identical post-upgrade.
- **Uninstall then reinstall** (both products): db files independently confirmed present on disk immediately after uninstall (`Test-Path` against the actual `.db` file, not inferred), then a fresh install followed by `needs_setup: false` and a successful login with pre-uninstall credentials, then (Clinic) a query proving the exact same patient record round-tripped through uninstall/reinstall unchanged.
- **Repair/reinstall over an existing install** (Retail): identical version reinstalled over itself; data and login unaffected.

## Schema/financial integrity across upgrade
- `/api/version`'s `schema_version: 1` and (Retail) `calculation_version: "retail-pricing-v2-wave0"` were unchanged before and after every install/upgrade/uninstall/reinstall cycle in this wave — no schema migration was exercised (none was needed; see `schema-migration-and-data-safety-report.md` for the dedicated migration-safety audit, which is a separate concern from installer behavior).
- Sale/patient records were compared field-by-field pre- and post- each operation via direct API queries against the real running app, not just "the file still exists."

## Result
PASS. No data loss observed across any install, upgrade, repair, uninstall, or reinstall performed this wave, for either product, verified against real running instances with real (synthetic) financial and patient records.
