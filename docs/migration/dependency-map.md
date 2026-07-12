# Dependency Map — Aura FullSuits Extraction

Maps what Retail and Clinic actually import/touch inside the large SOURCE monolith, so extraction can either (a) copy the dependency verbatim, (b) copy-and-trim a shared file down to the relevant slice, or (c) leave it behind and design a small adapter.

## 1. Files Retail and Clinic both depend on (shared — must travel, in trimmed form)

| Shared file | Used for | Extraction treatment |
|---|---|---|
| `api/mt_auth.py` (361L) | Session creation, `company_id` tenant scoping, `_is_module_enabled` (license/module gate), `mt_login_required`, `mt_require_subsystem`, `require_clinic_role` | Copy into `commercial_runtime/identity/mt_auth.py`; keep all decorators, both products import from here |
| `core/security/app_secret.py` | Per-install CSPRNG secret key (already hardened, no shared literal) | Copy verbatim into `commercial_runtime/security/` |
| `core/security/passwords.py` | Password hashing (PBKDF2, with legacy SHA-256 upgrade path per Phase 1 retail remediation) | Copy verbatim |
| `core/security/audit.py` | Login/audit event logging | Copy verbatim |
| `database/registry_db.py` | `registry.db` — `users`, `company_modules`, `user_permissions` tables; backs `mt_auth.py`'s tenant/license checks | Extract the registry schema + minimal accessor functions only (not the whole file, which also owns legacy `DOMAINS` catalog rows unrelated to Retail/Clinic) |
| `database/subsystem_db.py` (6,358 lines total) | Owns `init_retail`/`_seed_retail` (lines 4732-4941) and `init_clinic`/`_seed_clinic` (lines 5103-5390), plus shared low-level helpers: `_conn()`, `_get_path()`, `sub_create()`, `get_accounting_conn()`, `get_retail_conn()`, `get_clinic_conn()`, `_is_standalone()` | **Biggest single coupling point.** Extract only: the two `init_*`/`_seed_*` functions + the generic connection/path helpers they call. Do NOT copy the file wholesale (it also owns HR/CRM/Accounting/PM/Marketing/Inventory schema — ~6,000 unrelated lines) |
| `core/rbac/roles.py`, `core/rbac/permissions.py` | Role catalogs — `retail` dict (5 roles) and `clinic` dict (7 roles, currently unused beyond the binary doctor gate) | Extract only the `"retail"` and `"clinic"` dict entries into small per-product role-catalog modules |
| `android/app/build.gradle`, `android/build.gradle`, `android/settings.gradle`, `android/gradle.properties`, `gradlew`/`gradlew.bat`, `android/gradle/wrapper/` | Single Gradle project, two product flavors | Copy the whole `android/` tree once; both products live in it (matches SOURCE's actual architecture — do not split into two Android projects) |
| `android/app/src/main/java/.../net/{ApiClient,AuraApi,Models}.kt`, `server/ServerBootstrap.kt`, `ui/theme/*`, `ui/i18n/*`, `ui/components/Components.kt`, `ui/screens/{LoginScreen,SettingsScreen,SetupScreen}.kt`, `MainActivity.kt`, `AssetInstaller.java` | Shared Android infra used by both flavors | Copy verbatim |
| `config.py` (300L) | `BASE_DIR`/`AURA_APP_DATA`/`AURA_STANDALONE` resolution, `SECRET_KEY` wiring, `DEFAULT_USERS` demo seed | Copy + strip the `DOMAINS` dict (banking/healthcare/education/manufacturing — unrelated legacy verticals) |
| `requirements.txt` | flask, werkzeug, flask-cors, flask-login, openpyxl, waitress/gunicorn, requests (relevant) vs flask-socketio family, eventlet, pandas (likely irrelevant, unconfirmed) | Curate into `requirements/base.txt`; confirm socketio/pandas are unused before dropping |

## 2. Files Retail depends on that Clinic does not

| File | Used for |
|---|---|
| `api/subsystems/retail_api.py` | All POS routes |
| `core/retail/pricing.py` | Tax/pricing calculation (has dedicated unit tests) |
| `static/js/subsystem-retail.js` | Web POS UI |
| `android/app/src/main/java/.../ui/screens/{RetailScreens,RetailExtraScreens,BarcodeScanner}.kt` | POS UI + barcode scanning |
| CameraX (1.3.4) + ML Kit barcode-scanning (17.3.0) Gradle deps | Camera barcode capture |
| `tests/retail_pricing_test.py`, `tests/retail_security_test.py` | Existing test coverage |
| `docs/retail/RETAIL_SECURITY_PHASE_1.md` | Prior security remediation record |

## 3. Files Clinic depends on that Retail does not

| File | Used for |
|---|---|
| `api/subsystems/clinic_api.py` | All clinic routes |
| `static/js/subsystem-clinic.js` | Web clinic UI |
| `android/app/src/main/java/.../ui/screens/{PatientsScreen,PatientDetailScreen,AppointmentsScreen,ClinicExtraScreens}.kt` | Clinic UI |
| **Cross-import into Accounting**: `clinic_api.py` locally imports `sub_create`, `get_accounting_conn` from `database/subsystem_db.py` to mirror clinic invoices/lab-expenses into the platform's `transactions`/`invoices` tables | Best-effort accounting sync (non-blocking, try/except-wrapped) — this is a real functional dependency on Accounting's schema existing, not just a code coupling |

## 4. Cross-subsystem reach that must be cut or adapted (not shared infra — genuine coupling to unrelated large-platform modules)

| Coupling | Where | Resolution |
|---|---|---|
| Clinic → Accounting write | `clinic_api.py:517,589` (`get_accounting_conn`, raw inserts into `transactions`/`invoices`) | Either (a) cut this feature in the extracted Clinic (lose "shows up in Accounting too"), or (b) replace with a small outbound adapter/webhook interface. **Do not silently keep a hard dependency on the full Accounting subsystem** — that would violate "Clinic must not become tightly coupled" intent by proxy (Accounting isn't Retail, but it's still a large unrelated platform module) |
| Clinic → internal event bus | `clinic_api.py:29` — `requests.post('http://127.0.0.1:5000/api/events/emit', ...)` hardcoded to same-process port 5000 | Needs a config value or removal; a standalone Clinic won't have this event endpoint unless explicitly rebuilt |
| Both → `database/subsystem_db.py` monolith | See §1 | Surgical function extraction, not whole-file copy |
| Both → `core/rbac/{roles,permissions}.py` monolith | Shared multi-subsystem role catalog file | Extract only relevant dict keys |

## 5. Explicitly NOT needed (large-platform modules Retail/Clinic never touch)

Based on discovery, these `core/` directories have zero references from `retail_api.py`, `clinic_api.py`, `core/retail/`, or the Android screens investigated: `core/accounting/` (except the one cross-import noted above), `core/hr/`, `core/crm/`, `core/ai/`, `core/lifecycle/`, `core/discovery/`, `core/registry_system/`, `core/plugin_manager/`, `core/runtime/`, `core/automation/`, `core/integration/`, `core/ui/`. None of these should be imported into `aura-fullsuits`. If any turns out to be a hidden transitive dependency during Phase 2/3 test runs, treat that as a bug in this map to correct, not a reason to bulk-import the platform.

## 6. Dependency direction rule going forward

Retail and Clinic must not import from each other (hard constraint). Where both need the same thing (auth, tenant identity, security primitives, Android shell), it lives in `commercial_runtime/` and both products depend on `commercial_runtime/`, never on each other directly.
