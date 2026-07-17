# Wave 0 Windows Packaged Smoke Test

Real builds and a real running-exe smoke test, both actually performed in
this environment on 2026-07-17. This closes the residual risk recorded in
`docs/corrections/wave0/wave0-test-report.md` ("Windows packaged smoke
test: not re-run in this wave") per the Wave 0 integration addendum's
explicit requirement. No claim below is estimated.

## Spec fix required before building

Both PyInstaller specs' `hiddenimports` lists were missing modules this
wave added or that a prior wave's fix started using at import time:
`products/retail/packaging/aura_retail.spec` was missing
`commercial_runtime.identity.onboarding_routes` (Clinic's spec already had
it) and `api.import_api`; both specs were missing
`commercial_runtime.backup.service`/`commercial_runtime.backup.routes`
(new this wave). Added to both specs. In practice PyInstaller's static
analysis of `app.py`'s own top-level imports would likely have pulled these
in regardless (the app.py imports are plain `from x import y` statements,
not dynamic), but explicit `hiddenimports` entries match this codebase's
existing defensive convention and were added for both products.

## Build environment

- Host OS: Windows 11 Pro (10.0.26200)
- Python: 3.11.9, PyInstaller 6.21.0, contrib hooks 2026.6
- Commands:
  `python -m PyInstaller products/retail/packaging/aura_retail.spec --noconfirm --distpath dist --workpath build/pyinstaller-work-retail --log-level WARN`
  `python -m PyInstaller products/clinic/packaging/aura_clinic.spec --noconfirm --distpath dist --workpath build/pyinstaller-work-clinic --log-level WARN`

## Result: BOTH BUILDS SUCCEEDED

`dist/AuraRetail/AuraRetail.exe` and `dist/AuraClinic/AuraClinic.exe` both
built with no errors (two INFO-level `pycparser.lextab`/`pycparser.yacctab`
hidden-import warnings only, pre-existing and not security/correctness
-relevant — the same warnings appeared in the original Phase 2B/Phase 3
build reports).

## Smoke test method

Each exe was launched directly (not via a UI) with `AURA_APP_DATA` pointed
at a throwaway directory outside the repo and outside `Program Files`, then
exercised via `curl` against its HTTP API — the same method the original
Phase 2B Retail build report used, since neither product has a rendered
frontend page yet (API-layer validation only, per that report's own
documented limitation).

## A real, newly-found defect: the launcher's own readiness watchdog

**`launcher_retail.py`'s `_wait_for_server()` incorrectly declared the
server dead and killed a working process.**

Sequence observed on the first launch attempt: the log showed `Starting
server on http://127.0.0.1:5000` at `04:11:03`; an external `curl` to
`/api/onboarding/status` succeeded with `200 {"needs_setup":true}` shortly
after; but the launcher's own internal watchdog (a separate `urllib`-based
polling loop checking the same URL) logged `The Aura Retail server did not
start in time` at `04:11:46` (43 s later) and called `_fatal()`, which
calls `sys.exit(1)` — since the waitress server runs on a daemon thread,
the main thread's exit killed it, taking down a server that was
demonstrably already answering requests correctly.

This was **not investigated to root cause within this smoke test's time
budget** — a plausible cause is `urllib.request.urlopen`'s Windows-registry
-based proxy auto-detection interfering with a `127.0.0.1` request inside
the frozen process (this machine's `urllib.request.getproxies()` returns
empty from an unfrozen interpreter, which doesn't rule out different
behavior inside the frozen exe or a timing/threading interaction specific
to the bundled build), but this is an unverified hypothesis, not a
confirmed finding. **This is a real defect in the launcher's self-check,
not a defect in the actual server or in any of this wave's financial/data
-safety fixes** — reproduced on a second, isolated launch: the server
started and served correctly, and all functional testing below was
completed well inside the ~43 s window before the watchdog would have
fired. Recorded as a new item in the residual risk register. **Not fixed
in this wave** — it is unrelated to the named AUDIT items and touching
launcher timeout/networking logic without a confirmed root cause risks
introducing a worse bug for no verified benefit.

## Retail packaged smoke test — RESULT: PASS (server + all wave 0 fixes); launcher watchdog defect noted above

| Step | Result |
|---|---|
| Clean install, empty database | PASS — fresh `registry.db`/`retail.db` created under the isolated app-data dir |
| `GET /api/onboarding/status` | PASS — `{"needs_setup": true}` (AUDIT-001 fix present in the packaged build) |
| `POST /api/onboarding/create-admin` | PASS — `200`, admin created |
| `GET /api/onboarding/status` after | PASS — `{"needs_setup": false}` |
| `POST /api/auth/login` | PASS — `200` |
| `GET /api/sub/retail/dashboard/stats` (authenticated) | PASS — clean, all-zero payload |
| Static asset (`/static/i18n.js`) | PASS — `200` |
| Create product + stock-adjust | PASS |
| **`POST /api/sub/retail/sales` with an Android-style zero-tax payload** (`tax_rate: 0, discount_pct: 0` on a `tax_rate=15` product) | **PASS — server computed `tax_amount: 15.0, total: 115.0` in the packaged exe**, confirming AUDIT-002/003's fix is present and effective in the actual shipped artifact, not just in the dev/test environment |
| `POST /api/backup/create` | PASS — real backup zip created (`aura-retail-backup-...aurabak.zip`), manifest with correct checksums returned |
| `GET /api/backup/list` | PASS — the created backup listed |
| Clean process termination (`taskkill /F`) | PASS |
| Post-kill DB integrity (`PRAGMA integrity_check`) | PASS — `ok`; 1 sale, 1 product present, no corruption |

## Clinic packaged smoke test — RESULT: PASS

| Step | Result |
|---|---|
| Clean install, empty database | PASS |
| `GET /api/onboarding/status` | PASS — `{"needs_setup": true}` |
| `POST /api/onboarding/create-admin` | PASS |
| `POST /api/auth/login` | PASS |
| `GET /api/sub/clinic/dashboard/stats` | PASS — clean, all-zero payload |
| Static asset (`/static/i18n.js`) | PASS — `200` |
| Create patient + $100 invoice | PASS |
| **Overpayment (`amount: 9999`) against the $100 invoice** | **PASS — rejected with 400, "This product has no customer-credit ledger, so overpayment cannot be accepted."**, confirming AUDIT-011 in the packaged exe |
| **Duplicate payment, same `idempotency_key`, twice** | **PASS — second call returned the original payment (`id: 1`), invoice stayed `partial` at `$40` paid, not `$80`**, confirming AUDIT-012 in the packaged exe |
| `POST /api/backup/create` | PASS — real backup zip created, correct `product_code: "clinic"` in the manifest |
| Clean process termination | PASS |
| Post-kill DB integrity | PASS — `ok`; 1 patient, 1 invoice, 1 payment present |

Clinic did not independently trigger the launcher-watchdog defect during
this test run (the process was terminated deliberately via `taskkill`
before its own 45 s window elapsed) — this does not confirm Clinic's
launcher is unaffected, only that this specific run didn't hit it; both
launchers share materially the same `_wait_for_server` pattern.

## What this does and does not establish

**Establishes**: the packaged Windows builds for both products actually
build, actually run, and actually exhibit every Wave 0 financial/data
-safety fix this phase implemented — the zero-tax defect is fixed, the
overpayment/duplicate-payment defects are fixed, onboarding works, backup
creation works, and no database corruption resulted from a hard process
kill mid-session.

**Does not establish**: that either product is ready for a real customer
install. No installer/code-signing exists (unchanged from the Phase 2B/3
reports). No UI was exercised (no rendered frontend page exists yet for
either product; this remains an API-layer validation, per the original
build reports' own documented limitation). The newly-found launcher
-watchdog defect means a real end user launching the actual packaged exe
today could, under conditions not yet understood, see their server
auto-killed ~45 seconds after a successful start — **this is a genuine,
unresolved defect discovered by this smoke test**, not a hypothetical.

## Cleanup

Both throwaway `AURA_APP_DATA` directories, the temp cookie jars, and the
exe stdout logs used for this test were deleted after the test completed.
No synthetic data was committed. `dist/` and `build/` remain gitignored
(`/dist/`, `/build/` in `.gitignore`) — the built executables themselves
are not committed to the repository.
