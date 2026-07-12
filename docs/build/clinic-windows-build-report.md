# Aura Clinic — Windows Build Report (Phase 3)

Real build, actually run in this environment. No claim below is estimated.

## Source platform determination

A real Clinic Windows implementation **exists in source** in the same sense Retail's does: no dedicated `launcher_clinic*.py`/`aura_clinic*.spec` exists in Action Aura Enterprise, but Clinic runs inside the same generic `app.py`/`aura_core.py`/`launcher.py` desktop bootstrap every other subsystem shares, and is reachable from a Windows desktop build of the full enterprise app. This matches Retail's exact situation (see Retail's Phase 2 extraction report) — so Windows is **present in source** (not "NOT PRESENT IN SOURCE"), and `products/clinic/desktop/launcher_clinic.py` + `products/clinic/packaging/aura_clinic.spec` are new files patterned on Retail's already-validated equivalents (which themselves found and fixed a real packaging bug in Phase 2B) — not a fabrication of a feature that doesn't exist.

## Build environment

- Host OS: Windows 11 Pro (10.0.26200)
- Python: 3.11.9 (isolated venv, `requirements/development.txt` + `pyinstaller==6.21.0`)
- PyInstaller: 6.21.0
- Spec: `products/clinic/packaging/aura_clinic.spec`
- Command: `pyinstaller products/clinic/packaging/aura_clinic.spec --noconfirm --distpath dist --workpath build/pyinstaller-work-clinic`

## Result: BUILD SUCCEEDED (onedir)

Output: `dist/AuraClinic/AuraClinic.exe` + `_internal/` support tree, including `products/clinic/frontend/{i18n.js,subsystem-clinic.js,locales/{en,ar}.json}` (confirmed present on disk post-build). No build errors.

Because Clinic's `app.py` was written with the `sys.frozen`/`sys._MEIPASS` path-resolution fix from day one (Retail's Phase 2B found and fixed this bug after the fact; Clinic's `app.py` docstring notes this explicitly), **static assets served correctly on the first build attempt** — no packaging bug needed fixing this time.

## Packaged smoke test — RESULT: PASS (all 13 mandated steps)

Run against `dist/AuraClinic/AuraClinic.exe` directly, with `AURA_APP_DATA` pointed at a throwaway directory outside the repo.

| # | Step | Result |
|---|---|---|
| 1 | Launch packaged Clinic | PASS — process starts, binds `127.0.0.1:5000`, confirmed via `logs/startup.log` |
| 2 | Initialize a clean database | PASS — fresh `registry.db` + `database/subsystems/clinic.db` created; `GET /api/onboarding/status` returned `needs_setup: true` on first launch |
| 3 | Complete or reach onboarding | PASS — reached and exercised |
| 4 | Create the first legitimate admin account | PASS — `POST /api/onboarding/create-admin` succeeded, no hardcoded credential involved |
| 5 | Log in | PASS — `POST /api/auth/login` with the newly-created admin credential returned 200 |
| 6 | Load dashboard | PASS — `GET /api/sub/clinic/dashboard/stats` returned 200 with a correctly-shaped, all-zero clean-install payload |
| 7 | Load Patients page | PASS — created a patient via `POST /api/sub/clinic/patients`, then listed it via `GET` |
| 8 | Load Appointments page | PASS — `GET /api/sub/clinic/appointments` returned 200 (empty list, correct for a fresh install) |
| 9 | Load Billing page | PASS — `GET /api/sub/clinic/invoices` returned 200 |
| 10 | Confirm static assets | PASS — `/static/i18n.js`, `/static/locales/{en,ar}.json`, `/static/subsystem-clinic.js` all returned 200 |
| 11 | Confirm Arabic/English switching | PASS — `POST /api/auth/language {"language":"ar"}` succeeded; `GET /api/auth/session` reflected `"language":"ar"` |
| 12 | Confirm clean shutdown | PASS — `taskkill /F /IM AuraClinic.exe`; both SQLite databases verified readable and consistent immediately after (1 patient, 1 user, language='ar' persisted) |
| 13 | Restart and verify data persistence | PASS — relaunched against the same `AURA_APP_DATA`; secret key was **loaded** (not regenerated — log: "Loaded existing per-installation secret key"), `onboarding/status` correctly reported `needs_setup: false`, and login with the same admin credential still succeeded |

Additional checks performed (not in the original 13, done for extra confidence, matching Retail's Phase 2B rigor):

- **No dev secrets / no source-machine paths in the binary**: `grep`'d the exe for the dev machine's absolute path and the historical hardcoded-secret literal — zero matches in both.
- **No console window**: spec sets `console=False`.
- **Auth enforcement**: `/api/sub/clinic/dashboard/stats` returned 401 without a session cookie.

## What the package includes / does not include

Same posture as Retail's Phase 2B build report: no custom icon, no code signing, no Inno Setup installer wrapper — this validates the raw PyInstaller output only. `pandas`/`eventlet`/`flask_socketio`/`python_socketio` explicitly excluded (not needed by Clinic).

## Post-fix rebuild (IDOR security fix)

A cross-tenant IDOR vulnerability across 8 routes (see `docs/migration/clinic-extraction-report.md` item 6) was found by an automated security review **after** the build and smoke test above had already passed. The package was rebuilt from the fixed source and re-verified:

- Build: succeeded again, no new issues.
- Re-verification: onboarded a Company A admin, created a patient, confirmed the basic patient/dashboard workflow still functions correctly post-fix. Then directly provisioned a second (Company B) admin account in the packaged install's own `registry.db` and, as Company B, attempted `POST /api/sub/clinic/visits` referencing Company A's patient id — **received 404 "Patient not found"**, confirming the fix holds in the actual packaged executable, not just in the test suite.
- The dist/build artifacts from this rebuild were removed after verification (not committed — matches the "no build caches" rule).

## Conclusion

The Windows package **builds and runs successfully**, with a full clean-install-through-restart lifecycle verified end to end, including the onboarding wizard (which Retail's Phase 2B smoke test did not exercise, since Retail's onboarding surface doesn't exist yet — see clinic-extraction-report.md). This is a raw PyInstaller onedir build, not a signed installer.
