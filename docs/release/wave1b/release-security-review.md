# Release Security Review (Wave 1B, Part S)

Scope: `products/retail/backend`, `products/clinic/backend`, `commercial_runtime`, both Android manifests/Gradle configs, both Inno Setup installer scripts, `products/run_all_tests.py`, both PyInstaller `.spec` files. Excludes `__pycache__` and `*/tests/*` unless noted.

## Findings

| # | Check | Result |
|---|---|---|
| 1 | Flask debug mode | Clean — `app.run(host='127.0.0.1', port=port, debug=False)` in both `app.py` |
| 2 | Hardcoded password/secret literals | Clean |
| 3 | Demo/seed default credentials (admin@/test@/demo@/password123/changeme) | Clean |
| 4 | Debug-only/backdoor routes (`/api/dev/`, `/api/_debug`) | Clean |
| 5 | Absolute dev-machine paths in shipped code | Clean |
| 6 | Hardcoded localhost/private-IP URLs | Clean |
| 7 | Android `debuggable` flag | `debuggable true` only inside `debug {}`; `release {}` explicitly sets `debuggable false` in both `build.gradle` files |
| 8 | Exported Android components | Only `MainActivity` (`android:exported="true"`), required for the `MAIN`/`LAUNCHER` intent-filter of a launcher activity. No services/receivers/providers declared in either manifest — nothing else exported |
| 9 | Android auto-backup | `android:allowBackup="false"` in both manifests — patient/business SQLite data cannot be swept into Android's cloud auto-backup |
| 10 | Writable-executable-directory assumption | Both installers use `DefaultDirName={autopf}\Action Aura\Aura {Retail,Clinic}` (Program Files) with `PrivilegesRequired=lowest`/`PrivilegesRequiredOverridesAllowed=dialog`; all runtime data/writes go to `%LOCALAPPDATA%\Aura{Retail,Clinic}`, never back into the install directory — no reliance on the app folder being writable at runtime |
| 11 | Insecure installer custom actions | `[Run]`/`[UninstallRun]` in both `.iss` files only ever invoke a fixed-string `taskkill /IM Aura{Retail,Clinic}.exe /F` via `{cmd} /C`, plus the postinstall app launch — no dynamic/user-controlled command construction |
| 12 | Path traversal / command injection in installer or build scripts | `.iss` `[Code]` only uses `ExpandConstant('{localappdata}\...')` with fixed literal suffixes, no external input; `products/run_all_tests.py` calls `subprocess.run([sys.executable, '-m', 'pytest', str(path), ...])` as a list (no `shell=True`, no string concatenation) |
| 13 | Verbose/sensitive logging | No logging call outputs a password/secret/token/PIN value; the one grep hit (`commercial_runtime/security/app_secret.py`) is a logger-name declaration (`getLogger("aura.security.secret")`), not a value being logged |
| 14 | Release artifact contents | PyInstaller `.spec` `Analysis(datas=[...])` bundles only each product's `frontend/` static files — no `.git`, tests, keystores, passwords, or databases; Inno Setup `[Files]` sources only `{#DistDir}\*` (the PyInstaller output folder), not the repository, so nothing outside the built app can end up in the installer |

## Conclusion
No unresolved security findings from this pass. All checklist items in the Wave 1B spec's Part S ("unexpected exported components, unrestricted backup behavior, writable executable directory assumptions, insecure installer custom actions, path traversal, command injection in installer/build scripts") were checked and are clean. Combined with the two real defects already found and fixed earlier in this wave — the Inno Setup `/SUPPRESSMSGBOXES` silent-uninstall data-loss risk (`InitializeUninstall()`'s `if UninstallSilent() then Exit;` guard) and the receipt-data XSS/injection risk (`_lastSaleData` fix) — this review found no *additional* issues requiring a fix.

Not in scope for this review (unchanged from earlier waves, not re-audited here): authentication/authorization logic, SQL construction patterns, and the multi-tenant isolation model — those were covered by prior audit passes referenced elsewhere in `docs/`.
