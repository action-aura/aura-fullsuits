# Windows Product Audit

Consolidates this audit's own findings with the real Phase 2B/3 packaged-exe
verification (`docs/build/retail-windows-build-report.md`,
`docs/build/clinic-windows-build-report.md`), which remains valid since
`products/`/`commercial_runtime` are unmodified since those builds (`01`).

| Item | Retail | Clinic |
|---|---|---|
| Launcher exists | PASS (`launcher_retail.py`) | PASS (`launcher_clinic.py`) |
| Startup speed | Not numerically measured in any phase; qualitatively fast (waitress + SQLite, no heavy framework init) — UNVERIFIED precisely |
| First-run flow | **FAIL — no onboarding route registered, see `12`** | PASS — real 13-step smoke test including first-run onboarding |
| Local server startup | PASS — `127.0.0.1`, waitress, confirmed via `logs/startup.log` in real runs | PASS, same |
| Single-instance behavior | PASS — named Windows mutex | PASS, same launcher pattern |
| Port conflicts | PASS — scans a 20-port range | PASS, same |
| Clean shutdown | PASS — real `taskkill` test, DB verified consistent after | PASS, same, plus a real restart-and-verify-persistence step |
| Orphan background processes | Not independently tested for a hard-crash (not clean-kill) scenario in any phase — UNVERIFIED |
| Database file location | PASS — `%LOCALAPPDATA%\Aura{Retail,Clinic}`, not inside the install directory | PASS, same |
| Writable paths | PASS — confirmed no writes attempted inside the install dir | PASS, same |
| Frozen resource paths (`sys._MEIPASS`) | PASS — the Phase 2B build found and fixed a real static-asset-404 bug here; verified fixed | PASS — written correctly from day one, no bug found |
| Static assets | PASS (after the Phase 2B fix) | PASS |
| Localization (en/ar) | PASS — real smoke test, genuine non-identical Arabic strings confirmed | PASS — real smoke test, language switching confirmed via session state |
| Printing | **NOT PRESENT** (`12`) | Not independently re-checked in this pass; treated as UNVERIFIED rather than asserted absent (Clinic's frontend was not re-grepped for `window.print` in this specific pass) |
| File dialogs | Not applicable — browser/native-window based UI, no native file-dialog code found | Same |
| Import | PASS (Retail only — `import_api.py`, real end-to-end test performed against the packaged exe in Phase 2B: "uploaded a real CSV... product landed in retail.db") | Not confirmed present for Clinic — not found in the routes sampled |
| Export | **NOT PRESENT IN SOURCE** (either product) | Same |
| Crash recovery | Assessed by code inspection only (WAL mode, explicit transactions on the well-behaved routes) — see `06`/`07` for the specific gaps found (`receive_purchase_order`, `create_invoice`, `record_payment`) | Same |
| Executable build | PASS — both build successfully via PyInstaller 6.21.0 onedir | PASS |
| Executable size | Retail: 5.4 MB exe + `_internal/` tree (per build report) | Not independently re-measured in this pass |
| Antivirus false-positive risk | UNVERIFIED (no AV product tested against) | UNVERIFIED |
| Unsigned executable warning | **FAIL — both unsigned, SmartScreen warning expected on real customer machines** | Same |
| Icon | **FAIL — default PyInstaller icon, no custom branding**, explicitly flagged as cosmetic in the Phase 2B report | Not independently re-confirmed this pass, presumed same posture (no custom-icon spec entry found) |
| Metadata (version info resource) | Not confirmed set in the `.spec` files — UNVERIFIED | Same |
| Installer availability | **FAIL — no installer wrapper, raw onedir only** | Same |
| Uninstall behavior | N/A — no installer means no uninstall entry either | Same |
| Update readiness | **NOT PRESENT** — explicitly out of scope for every phase to date, by design | Same |
| Windows 10/11 compatibility | Built and smoke-tested on Windows 11 Pro (10.0.26200) only; Windows 10 UNVERIFIED | Same |
| Offline behavior | PASS by architecture (fully local server + SQLite) | Same |
| WebView2/pywebview dependency | `launcher_*.py` tries `pywebview` first, falls back to Edge/Chrome `--app` mode, then default browser — a three-tier fallback, PASS in design; the actual native-window path (`pywebview`) was not confirmed to have actually succeeded vs. fallen through to a browser window in the specific smoke-test runs (logs would show which path was taken; not re-checked in this pass) — UNVERIFIED which exact display path was exercised |

## Summary

Windows Clinic is the stronger of the two products on this platform — it has
the only real, complete, end-to-end-tested first-run experience of either
product on either platform. Windows Retail's core POS/inventory/sales
functionality is solid and real-smoke-tested, but is currently **unreachable
by a real customer** due to the missing onboarding path (`12`) — a defect that
exists identically on Windows and Android since it's a backend registration
gap, not a platform-specific UI issue.
