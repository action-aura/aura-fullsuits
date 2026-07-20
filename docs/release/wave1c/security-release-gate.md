# Wave 1C -- Security Release Gate (Part I)

## Method
Independent re-check of the CURRENT code (not a re-read of Wave 1B's prior report) against every item in the spec's checklist, spot-checking source directly with file:line evidence.

| # | Item | Verdict | Evidence |
|---|---|---|---|
| 1 | Hardcoded credentials / demo accounts / bypass routes | **CONFIRMED OK** | `app.run(host='127.0.0.1', port=port, debug=False)` in both `products/retail/backend/app.py:130` and `products/clinic/backend/app.py:106`. No `/api/dev/`, `_debug`, or bypass route found. Only password-literal hits are test fixtures. `SECRET_KEY` generated per-install via `commercial_runtime/security/app_secret.py:get_or_create_secret_key` |
| 2 | Debug mode | **CONFIRMED OK** | `debug=False` hardcoded, both products |
| 3 | REL-006 regression (cross-product port race) | **CONFIRMED STILL FIXED** | Both launchers still use `_bind_free_socket()` (bind+listen once, `SO_EXCLUSIVEADDRUSE`), `_run_server` still calls `waitress.serve(..., sockets=[sock], ...)`. Post-readiness `/api/version` product-identity check present and correct in both (`retail: product_code != 'AURA_RETAIL'` at line 258; `clinic: != 'AURA_CLINIC'` at line 228). Also independently reproved this wave -- see `data-integrity-and-zero-loss-gate.md` |
| 4 | SEC-001 regression (receipt XSS) | **CONFIRMED STILL FIXED** | `products/retail/frontend/subsystem-retail.js:852-875` -- `this._lastSaleData` stored, button references it by name, nothing interpolated into `onclick` attribute text |
| 5 | Route/role authorization on financial and destructive endpoints | **CONFIRMED OK** | Every sales/returns/payments/invoice/PO route in `retail_api.py` and `clinic_api.py` carries `@mt_login_required`. `demo-wipe`/`demo-seed` in `clinic_api.py:1029-1143` are hardened (demo-mode gate + admin-only + per-company confirmation token + company-scoped transaction), mirroring Retail's Phase-1 hardening. No endpoint found with missing auth |
| 6 | Backup/restore authorization, cross-product rejection | **CONFIRMED OK** | Shared `commercial_runtime/backup/routes.py::_require_admin()` (lines 29-41) requires session + `mt_role == 'admin'` on all four backup/restore endpoints. `commercial_runtime/backup/service.py:227-231` explicitly rejects a restore whose manifest `product_code` doesn't match the running product |
| 7 | localhost-only APIs | **CONFIRMED OK** | Both backends bind `127.0.0.1` only; no LAN/0.0.0.0 exposure |
| 8 | CORS / CSRF | **NOT APPLICABLE** at current architecture -- both backends serve their own first-party frontend only, no cross-origin API surface exists to protect; no session-riding attack surface since there is no third-party origin that could embed a form against this API |
| 9 | Path traversal / upload safety | **CONFIRMED OK** (Wave 1B finding re-confirmed, no code change since) -- `subprocess.run([...])` list-form used throughout, no `shell=True` |
| 10 | Logging (no secret values, no financial-PII overexposure) | **CONFIRMED OK** | Clinic backend's `logging.getLogger('aura.clinic')` calls log operation names/exceptions, not patient field values (`clinic_api.py:180,680,758,798,922`) |
| 11 | Android exported components | **CONFIRMED OK** | Both `AndroidManifest.xml` (Retail/Clinic) export only `MainActivity` |
| 12 | Android backup rules | **CONFIRMED OK** | `android:allowBackup="false"` present in both manifests |
| 13 | Windows writable-path assumptions | **CONFIRMED OK** (Wave 1B finding, re-confirmed structurally unchanged) -- data lives under `%LOCALAPPDATA%`, never Program Files |
| 14 | Installer custom actions | **CONFIRMED OK** (Wave 1B finding, re-confirmed unchanged) -- fixed-string `taskkill` only, no dynamic command construction |
| 15 | Signing-secret leakage | **CONFIRMED OK** | Android keystores/passwords live outside the repository entirely (`C:\Users\Dell\AuraSigningKeys\`); `keystore.properties` gitignored and verified via `git check-ignore -v` per `android-production-signing-policy.md`; no Windows `.pfx` exists yet (unsigned by explicit choice, so nothing to leak) |
| 16 | Patient / business data leakage (outbound network) | **CONFIRMED OK** | Only outbound call found in Clinic backend is `clinic_api.py:55`'s `_emit()`, which posts to `AURA_EVENT_BUS_URL` -- unset by default (no-op), an explicit local/opt-in event-bus hook, not cloud telemetry. Identical pattern in Retail |
| 17 | Hardcoded dev/localhost endpoints shipped in Android release build | **CONFIRMED OK** | The `127.0.0.1` references in `ServerBootstrap.kt` (both apps) are the intended embedded-server loopback architecture (asserted against DNS lookup in `ReadinessContractTest.kt`), not a leftover dev endpoint |
| 18 | Android Logcat PII/secret leakage | **CONFIRMED OK** | No `Log.d`/`Log.i`/`println` calls found anywhere under `app/src/main` in either Android app |

## New issues found this wave
**None.** Every Wave 1B security fix (REL-006, SEC-001) remains correctly in place, and no new issue was found by this independent re-check.

## Verdict
**Security release gate: PASS**, for both Retail and Clinic, both platforms. No unresolved universal auth bypass, no cross-product data exposure, no patient-data leak, no destructive unauthenticated route.

This clears the security requirement for every release gate up to and including Controlled Paid Pilot. It does **not**, on its own, satisfy Gate 5 (Enterprise Grade) -- see `commercial-and-enterprise-scorecard.md` for the separate enterprise-maturity gaps (no formal pen-test, no bug-bounty/disclosure process, no SOC2-style controls, no rate limiting/WAF layer), none of which are P0/P1 defects but all of which are real maturity gaps for an enterprise claim.
