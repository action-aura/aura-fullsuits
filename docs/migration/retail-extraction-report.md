# Aura Retail Extraction Report (Phase 2)

Status: **Backend extracted and verified working.** Android/desktop-packaging build not yet run (no toolchain invoked in this pass — see "Unresolved issues").

> **Update (Phase 2B — Retail Extraction Completion and Parity Hardening):** every item this report originally flagged as "Deferred / not yet located" or "Unresolved" for import/export, localization, and Windows packaging has since been closed. See `docs/migration/retail-phase-2b-validation-report.md` for what was done, `docs/migration/retail-parity-matrix.md` for the area-by-area final status, and `docs/build/retail-windows-build-report.md` for the real PyInstaller build (one bug found and fixed: static-asset 404s under `sys.frozen`, root-caused to `Path(__file__)` being unreliable inside a PyInstaller archive). Test total after Phase 2B: **116/116 passing** (this report's original 73 + 25 new import/export + 18 new localization). The remaining open items from Phase 2 below are historical context for what Phase 2B started from — read the Phase 2B validation report for current status before acting on anything marked "not yet located" or "deferred" here.

## Result summary

`products/retail/` is now an independently runnable Flask backend + frontend for Aura Retail, requiring only `commercial_runtime/` from the rest of `aura-fullsuits` (no dependency on Action Aura Enterprise's `core/`, `api/`, or `database/` packages). All 73 ported tests pass against it (26 pricing/dashboard + 47 security), run from a clean venv with only `requirements/development.txt` installed.

```
pytest products/retail/tests/retail_pricing_test.py -v    # 26 passed
pytest products/retail/tests/retail_security_test.py -v   # 47 passed
```

## Copied files (byte-identical business logic, adapted imports only)

| File | Source | Destination | Change |
|---|---|---|---|
| Retail API routes | `api/subsystems/retail_api.py` (1501 lines) | `products/retail/backend/api/retail_api.py` | Import paths only (`api.mt_auth`→`commercial_runtime.identity.mt_auth`, `database.subsystem_db`→`database.schema`, `core.security.audit`/`modes`→`commercial_runtime.security.*`). `_emit()`'s hardcoded `http://127.0.0.1:5000/api/events/emit` made configurable via `AURA_EVENT_BUS_URL` (no-op by default — documented corrective, not silent, since standalone Retail has no event bus listening on that port). All route logic, SQL, and the tax/credit/PO/returns business rules are untouched. |
| Tax/discount engine | `core/retail/pricing.py` | `products/retail/backend/core/retail/pricing.py` | Byte-identical (only docstring path references updated). |
| POS web UI | `static/js/subsystem-retail.js` (1940 lines, includes the desktop HID/keyboard-wedge barcode scanner engine) | `products/retail/frontend/subsystem-retail.js` | Byte-identical copy. |
| Shared import wizard (client) | `static/js/import-wizard.js` | `products/retail/frontend/import-wizard.js` | Byte-identical copy (duplicated rather than shared — see "Deferred" below). |
| Security remediation doc | `docs/retail/RETAIL_SECURITY_PHASE_1.md` | `products/retail/docs/RETAIL_SECURITY_PHASE_1.md` | Unchanged. |

## Surgically extracted (function-level, from shared multi-subsystem files)

| Extracted | Source | Destination | Notes |
|---|---|---|---|
| `init_retail`, `_seed_retail` | `database/subsystem_db.py:4732-5081` | `products/retail/backend/database/schema.py` | Schema (17 tables) and demo seed logic copied verbatim, alongside the small generic helpers they need (`_conn`, `_get_path`, `_is_standalone`, `sub_create`, `get_retail_conn`) — extracted from the same file rather than importing the 6,358-line monolith. |
| `mt_login_required`, `mt_require_subsystem`, `require_clinic_role`, `authenticate_registry_user`, `create_session`, `_is_module_enabled` | `api/mt_auth.py` (361 lines) | `commercial_runtime/identity/mt_auth.py` | Copied whole (it was already a focused, single-purpose file) — retargeted at `commercial_runtime.security.*` and `commercial_runtime.identity.registry_db`. `require_clinic_role` is kept even though Retail doesn't use it, since Clinic (Phase 3) needs the identical function and this file is meant to be shared. |
| `users`, `company_modules`, `user_permissions`, `audit_logs` tables + `get_conn`/`init_registry_db`/`log_audit` | `database/registry_db.py` (~20 tables total) | `commercial_runtime/identity/registry_db.py` | Trimmed from ~20 tables to the 4 that `mt_auth`/`audit` actually touch. Everything else in the source file (EIP module catalog, document flow, numbering sequences, custom fields, secure onboarding links, licenses table) is platform-wide and not used by Retail's ported test suite — dropped, not carried forward as dead schema. |
| Login/logout/active-modules routes | `api/auth.py` (636 lines, mixed MT + legacy per-domain demo auth) | `commercial_runtime/identity/auth_routes.py` | Kept only the registry-based (multi-tenant) login path. Dropped: the legacy per-domain demo login (banking/healthcare/education/manufacturing — the unrelated industries-demo verticals), `register()`/`register-company`/`verify-invite`/`employee-setup` (SaaS self-service onboarding, not exercised by the ported tests, deferred), `preferences`/`me`/`users` (legacy-domain-session-only endpoints). |
| `dev_mode_enabled`, `retail_demo_mode_enabled` | `core/security/modes.py` | `commercial_runtime/security/modes.py` | Copied whole, unchanged (already self-contained). |
| `hash_password`, `verify_password`, legacy-SHA256 migration | `core/security/passwords.py` | `commercial_runtime/security/passwords.py` | Copied whole, byte-identical logic (PBKDF2-HMAC-SHA256, 600k iterations). |
| `record()` + event-type constants | `core/security/audit.py` | `commercial_runtime/security/audit.py` | Copied whole; retargeted its internal import at the trimmed `registry_db`. |
| `get_or_create_secret_key` | `core/security/app_secret.py` | `commercial_runtime/security/app_secret.py` | Byte-identical — this is the Phase-1-hardened per-installation secret, unchanged. |

## New files (not extracted — bootstrap glue that the source monolith didn't need in this shape)

| File | Why it's new |
|---|---|
| `products/retail/backend/app.py` | The source's entrypoint (`aura_core.init_app()` → `app.py`) boots every subsystem's blueprint in one process. A standalone product needs a much smaller Flask app that registers only `auth_bp` + `retail_bp`. Preserves the exact security-relevant config from the source `app.py`: per-install `SECRET_KEY`, `SESSION_COOKIE_HTTPONLY`/`SAMESITE`, 12h session lifetime, loopback-only CORS. |
| `products/retail/backend/config.py` | Trimmed from the source `config.py`: kept `BASE_DIR`/`AURA_APP_DATA`/`SECRET_KEY`/`IS_STANDALONE` resolution, dropped the `DOMAINS` dict (unrelated legacy demo verticals) and `DEFAULT_USERS` (only used by the legacy login path, which isn't ported — see risk register R12, now moot for this product). |
| `products/retail/desktop/launcher_retail.py` | New, patterned on the source `launcher.py`'s structure (native pywebview window → Edge/Chrome `--app` fallback → default-browser fallback, single-instance mutex guard, single free-port scan) but scoped to just Retail's own server bootstrap instead of the shared `aura_core` module (which handles bundle/seed logic for every subsystem). |
| `products/retail/packaging/aura_retail.spec` | New, self-contained PyInstaller spec (the source `aura_accounting_dev.spec` pattern *delegates* to the 9KB `aura_enterprise.spec`, which bundles every subsystem — not appropriate for a standalone product). **Not yet run** — see "Unresolved issues." |

## Test porting

`tests/retail_pricing_test.py` and `tests/retail_security_test.py` ported to `products/retail/tests/` with the same assertions; only the bootstrap section (which app module to import, updated module paths for the moved registry/security code) changed. Two tests from the source security suite were **not** ported, by deliberate scope decision, not oversight:

- `test_old_backdoor_credential_rejected` / `test_no_source_reference_to_backdoor_literals` — these guarded against a since-removed hardcoded backdoor in the source `api/auth.py`'s legacy per-domain login branch. That branch was not ported at all (see `auth_routes.py` above), so the vulnerability class doesn't exist in the extracted file to regress. `test_random_unknown_credential_rejected` (ported) still covers "unknown credentials are rejected."
- `test_demo_blueprint_not_registered_by_default_subprocess` / `test_demo_blueprint_registered_when_explicitly_enabled_subprocess` — these verified `api/demo_api.py`, a generic, unauthenticated, multi-subsystem demo portal (seeds/wipes CRM+HR+Retail+... with no auth) — explicitly out of scope for standalone Retail. Retail's own gated `demo-wipe`/`demo-seed` routes (inside `retail_bp` itself) are still fully covered by 5 ported tests.

## Deferred / not yet located (carried over from Phase 0's source-inventory gaps, now resolved by inspection)

- **HID/keyboard-wedge barcode scanner**: resolved — it's not a separate file, it's `_scan`/`_scannerDefaults`/`_isDesktopScanner` etc. inside `subsystem-retail.js` (lines ~584+), already copied whole.
- **Printing/sharing**: no distinct print/share implementation was found in the source (no `window.print()`, no Android share/print intent in the retail screens). This may be a planned-but-unbuilt feature in the source repo, not something this extraction dropped — flagged for the user to confirm rather than assumed.
- **Import/export**: the client (`ImportWizard.open('retail', ...)`) is copied (`import-wizard.js`), but its server-side handlers (`_handle_retail_products`, `_handle_retail_customers`, `_handle_retail_suppliers`, `_handle_retail_branches`, `_handle_retail_categories` in `api/import_api.py`, a 3000+ line shared multi-subsystem file) were **not** extracted in this pass — the import buttons in the copied frontend will 404 until this is done. Deferred to keep this phase reviewable; tracked as the first follow-up item for Phase 2 continuation.
- **`static/locales/{en,ar}.json`**: not yet copied/scoped-confirmed (source-inventory #38) — the Android-side Arabic i18n (`Strings.kt`) is a separate, already-portable system; the web-side locale files' scope (shared vs retail-only) still needs confirming before copying.

## Database compatibility

Schema is byte-identical to the source (`init_retail`'s `executescript`, unchanged). A database file produced by the source Action Aura Enterprise's Retail subsystem should open correctly against this extracted backend, since table/column definitions were copied verbatim and `_ensure_credit_schema`'s `ALTER TABLE IF NOT EXISTS`-style self-healing (inside `retail_api.py`) also carried over unchanged.

## Build status

- **Backend**: builds and runs (`python products/retail/backend/app.py`), verified via the full ported test suite (73/73 passing).
- **Desktop (PyInstaller)**: spec written, **not run** — no PyInstaller invocation was part of this pass. Do not treat this as a verified Windows build.
- **Android**: not touched in Phase 2 — scheduled for Phase 4 per `docs/migration/extraction-plan.md`.

## Unresolved issues

1. Import/export server-side handlers not yet extracted (see "Deferred" above) — highest-priority follow-up.
2. `aura_retail.spec` has not been run through PyInstaller in this environment.
3. `static/locales/*.json` scope (shared vs retail-only) unconfirmed.
4. `pywebview` is referenced by `launcher_retail.py` (matching the source `launcher.py` pattern) but is not in any `requirements/*.txt` — it was never in the source's `requirements.txt` either (installed separately for desktop packaging only, inferred from `launcher.py`'s `try/except ImportError` guard around `import webview`). Confirm the actual desktop build dependency list before the first real PyInstaller run.
