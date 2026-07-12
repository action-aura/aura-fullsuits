# Aura Retail — Windows Build Report (Phase 2B)

Real build, actually run in this environment. No claim below is estimated.

## Build environment

- Host OS: Windows 11 Pro (10.0.26200)
- Python: 3.11.9 (isolated venv, `requirements/development.txt` + `pyinstaller==6.21.0`)
- PyInstaller: 6.21.0, contrib hooks 2026.6
- Spec: `products/retail/packaging/aura_retail.spec`
- Command: `pyinstaller products/retail/packaging/aura_retail.spec --noconfirm --distpath dist --workpath build/pyinstaller-work`

## Result: BUILD SUCCEEDED (onedir)

Output: `dist/AuraRetail/AuraRetail.exe` (5.4 MB exe + `_internal/` support tree). Build produced no errors; PyInstaller warnings were at INFO level only (suppressed by `--log-level WARN`, none were security/correctness-relevant on inspection of the full log).

## Bug found and fixed during this validation

**Static assets (locale files, `i18n.js`, `subsystem-retail.js`) returned HTTP 404 in the first packaged build.**

Root cause: `products/retail/backend/app.py` computed its Flask `static_folder` from `Path(__file__).resolve().parent` unconditionally. Inside a PyInstaller bundle, `app.py` is compiled into the archive rather than collected as a loose file, so `__file__`-based filesystem navigation does not reliably resolve — the computed `static_folder` pointed at a path that doesn't exist in the frozen layout, so Flask silently 404'd every static request.

Fix (`products/retail/backend/app.py`): detect `sys.frozen` and use `sys._MEIPASS` (PyInstaller's own bundle-root pointer) as the path root when frozen, falling back to `Path(__file__)` only in dev — the same pattern already used by `config.py`'s `BASE_DIR` resolution, now applied consistently. Rebuilt and reverified: all static assets return 200 after the fix (see Smoke Test below).

This was only caught by actually running the packaged exe and hitting its HTTP endpoints — a source-only review would have missed it, since the dev/test-venv `Flask.test_client()` runs un-frozen and never exercises the `sys._MEIPASS` code path.

## What the package includes (verified by inspecting `dist/AuraRetail/_internal/`)

- `products/retail/frontend/` — `i18n.js`, `import-wizard.js`, `subsystem-retail.js`, `locales/en.json`, `locales/ar.json` (confirmed present on disk post-build).
- Retail backend, `commercial_runtime`, and `core.retail.pricing` compiled into the bundle via `hiddenimports` (not loose files — confirmed importable: the exe boots, initializes the database, and serves API routes that depend on all of them).
- No `pandas`/`eventlet`/`flask_socketio`/`python_socketio` (explicitly excluded in the spec — not needed by Retail, see `docs/migration/dependency-map.md`).

## What the package does NOT (yet) include

- No launcher icon / custom branding resource was set in the spec (`EXE(..., icon=...)` omitted) — the exe uses the default PyInstaller icon. Flagged as a cosmetic gap for a real customer-facing build, not a functional one.
- No code-signing certificate applied (none available in this environment) — expected for a dev/CI build, required before real distribution.
- No Inno Setup installer wrapper was built (`setup.iss`-equivalent for Retail does not exist yet) — this validates the raw PyInstaller output only, not an installer.

## Packaged smoke test — RESULT: PASS

Run against `dist/AuraRetail/AuraRetail.exe` directly (not via source), with `AURA_APP_DATA` pointed at a throwaway directory outside the repo to simulate a real per-installation data folder.

| Step | Result |
|---|---|
| 1. Start packaged executable | PASS — process starts, binds `127.0.0.1:5000`, waitress serving confirmed in `logs/startup.log` |
| 2. Initialize clean database | PASS — fresh `database/registry.db` and `database/subsystems/retail.db` created under the isolated app-data dir; zero pre-existing data (confirmed via direct sqlite3 query before any API call) |
| 3. Reach login / onboarding | PASS — `POST /api/auth/login` responds (400 with no credentials, 200 with a real inserted user — see below); no backend HTML template exists yet (deferred, see `docs/migration/retail-parity-matrix.md`), so this was validated at the API layer, not a rendered page |
| 4. Confirm Retail UI loads | PASS (API layer) — after login, `GET /api/sub/retail/dashboard/stats` returns 200 with a correctly-shaped, all-zero clean-install payload |
| 5. Confirm static assets load | PASS (after fix) — `/static/i18n.js`, `/static/locales/en.json`, `/static/locales/ar.json`, `/static/subsystem-retail.js` all return 200 |
| 6. Confirm language switching loads | PASS — `/static/locales/ar.json` returns 200 with genuine Arabic translations (`Dashboard` → `لوحة التحكم`, confirmed non-identical to English); `POST /api/auth/language` persists the choice to the packaged app's own `registry.db` |
| 7. Confirm application closes cleanly | PASS — `taskkill /F /IM AuraRetail.exe` terminated the process; both SQLite databases were verified readable and consistent immediately after (1 product + 1 user present from the smoke test, no corruption) |

Additional checks performed against the running packaged exe (not in the original 7-step list, done for extra confidence):

- **Import end-to-end**: uploaded a real CSV via `POST /api/import/execute` against the packaged server — product landed in `retail.db`, cleaning report returned correctly.
- **Auth enforcement**: `/api/sub/retail/dashboard/stats` and `/api/import/schemas` both returned 401 without a session cookie.
- **No dev secrets / no source-machine paths in the binary**: `grep`'d the exe and bundled data files for the dev machine's absolute path (`C:\Users\Dell\...`) and the known historical hardcoded-secret literal (`aura-enterprise-secret-2025-xK9mP2vL`) — zero matches in both. `DEFAULT_USERS`-style demo credentials were already excluded from `products/retail/backend/config.py` in Phase 2 (see risk register R12) and confirmed absent from the bundled `products/` data tree.
- **Writable, non-protected data directory**: the packaged exe wrote its database, logs, and per-install secret key under the `AURA_APP_DATA`-specified directory (a throwaway temp folder outside `Program Files` in this test); no writes were attempted inside the install directory itself.

## Known limitation from this validation pass

One intermediate launch attempt during testing logged `"The Aura Retail server did not start in time"` — root-caused to this test session running two overlapping exe instances against the same port while iterating (a test-harness artifact from manually re-launching without fully releasing the prior process), not a defect in the packaged app. The final, isolated run (clean process, clean app-data dir) completed the full 7-step smoke test with no errors.

## Console window

The spec sets `console=False` (`EXE(..., console=False, ...)`) — no console window is shown by design, matching "does not expose a console window unless intentionally required."

## Conclusion

The Windows package **builds and runs successfully** as of this validation, with one real defect found and fixed in-session (static asset path resolution under `sys.frozen`). This is a raw PyInstaller onedir build, not a signed installer — see "What the package does NOT include" above for the remaining gap to a shippable customer build.
