# Aura Clinic — Source Inventory (Phase 3)

Source repo (`SOURCE`): `c:\Users\Dell\Desktop\AuraEnterprise\AuraEnterprise` (read-only for this phase).
Destination: `products/clinic/` in `aura-fullsuits`.

Builds on Phase 0's clinic discovery (`docs/migration/source-inventory.md` items 18-30) — this document goes deeper and adds `api/standalone_auth.py`, which Phase 0/2 did not fully inventory.

**Reminder from Phase 0**: `api/subsystems/healthcare_api.py` / `database/healthcare.db` is an unrelated legacy demo module that also matches "healthcare" — not touched here, not Clinic.

## Clinic-specific code (extracted into `products/clinic/`)

| Component | Source | Lines | Destination |
|---|---|---|---|
| Clinic API routes | `api/subsystems/clinic_api.py` | 768 | `products/clinic/backend/api/clinic_api.py` |
| Clinic DB schema + seed | `database/subsystem_db.py:5109-5390` (`init_clinic`, `_seed_clinic`, `get_clinic_conn`) | ~280 | `products/clinic/backend/database/schema.py` |
| Clinic web UI | `static/js/subsystem-clinic.js` | 1448 | `products/clinic/frontend/subsystem-clinic.js` |
| Clinic role catalog (unwired, reference only) | `core/rbac/roles.py:166-194`, `core/rbac/permissions.py` (matching block) | ~30 | Documented in `docs/security/clinic-rbac-matrix.md`, not code-ported (confirmed unused by any enforcement path — see that doc) |

## Genuinely shared commercial-runtime code (already extracted in Phase 2, reused as-is)

| Component | Already in `commercial_runtime/` | Notes |
|---|---|---|
| `mt_login_required`, `mt_require_subsystem`, `require_clinic_role`, `authenticate_registry_user`, `create_session`, `_is_module_enabled` | `commercial_runtime/identity/mt_auth.py` | `require_clinic_role` was ported in Phase 2 even though Retail doesn't use it — exactly for this moment. No changes needed. |
| Password hashing, audit, secret key, runtime modes | `commercial_runtime/security/*` | Reused unchanged. |
| `users`, `company_modules`, `user_permissions`, `audit_logs` tables | `commercial_runtime/identity/registry_db.py` | Reused unchanged. `users.clinic_role` column already present (added in Phase 2 for forward-compat — this is that moment). |
| Login/logout/language persistence | `commercial_runtime/identity/auth_routes.py` | Reused unchanged. |

## Newly extracted shared commercial-runtime code (Phase 3 addition)

`api/standalone_auth.py` (698 lines) was flagged in the Phase 2B report as "the file whose own docstring says it is what the shipped Retail/Clinic standalone products actually run" — only its `set_language` route had been ported. Full read in Phase 3 confirms: this file is **onboarding + admin employee management**, generic across both products, not Clinic-specific. Genuinely required by Clinic (Retail has the same gap, documented but intentionally not retrofitted this phase per the task's Retail-freeze instruction).

Extracted into `commercial_runtime/identity/`:

| Route | Purpose | New location |
|---|---|---|
| `GET /api/onboarding/status` | DB-is-source-of-truth first-run check | `auth_routes.py` |
| `POST /api/onboarding/create-admin` | Create the first admin account, auto-login | `auth_routes.py` |
| `POST /api/onboarding/complete` | Mark onboarding wizard done | `auth_routes.py` |
| `GET /api/auth/session` | Session + per-account language check | `auth_routes.py` |
| `GET/POST /api/admin/employees` | List / create staff (incl. `clinic_role` field) | `auth_routes.py` |
| `PUT /api/admin/employees/<id>/status` | Enable/disable a user, bumps `session_version` | `auth_routes.py` |
| `PUT /api/admin/employees/<id>/clinic-role` | Set doctor/secretary/none | `auth_routes.py` |
| `POST /api/admin/employees/<id>/permissions` | Legacy per-subsystem access level | `auth_routes.py` |
| `GET /api/admin/audit` | Filtered audit log query | `auth_routes.py` |
| `GET /api/admin/stats` | Admin dashboard KPIs | `auth_routes.py` |
| `POST /api/auth/employee/setup` | Employee accepts an invite link, sets password | `auth_routes.py` |

New tables added to `commercial_runtime/identity/registry_db.py`: `secure_links` (invite tokens), `company_settings` (locale/business fields written by onboarding).

**Not extracted** (source routes intentionally dropped, not silently — see rationale):
- `GET /api/admin/employees/<id>/permissions/named`, `GET /api/admin/rbac/roles`, `POST /api/admin/rbac/assign-role`, `POST /api/admin/rbac/migrate` — these hard-depend on `core/rbac/engine.py` (a "named permission grants" system layered on top of the legacy `user_permissions` table). Phase 0 already established this richer system is **not wired into any actual enforcement** for Clinic (only the binary `require_clinic_role('doctor')` gate is real) — porting an admin UI for a permission system nothing enforces would be dead weight, not parity. `create_employee`'s own optional `RBACEngine` call is wrapped in `try/except: pass` in source (graceful degradation by design) — preserved as-is, so omitting `core/rbac/engine.py` doesn't break anything, it just means that best-effort block always no-ops, exactly matching source's own fallback path when the module is unavailable.

## Unrelated enterprise code (confirmed out of scope, not touched)

`core/accounting/*` (Clinic's lab-expense/invoice sync targets this, see dependency map), `core/hr/*`, `core/crm/*`, `core/ai/*`, `core/lifecycle/*`, `core/discovery/*`, `core/registry_system/*`, `core/plugin_manager/*`, `core/rbac/engine.py` (see above), `api/subsystems/healthcare_api.py`.

## Android-only, deferred to Phase 4 (not touched)

`android/app/src/main/java/com/actionaura/enterprise/ui/screens/{PatientsScreen,PatientDetailScreen,AppointmentsScreen,ClinicExtraScreens}.kt`, `net/AuraApi.kt` clinic endpoints, `net/Models.kt` clinic DTOs, the `clinic` Gradle flavor (`com.actionaura.clinic`). Confirmed present in source (Phase 0), confirmed NOT copied, edited, or built in this phase.

## Missing / non-existent features (confirmed absent from source, not fabricated here)

- **No document/attachment upload** for patients, visits, or prescriptions anywhere in `clinic_api.py` or `subsystem-clinic.js` — no file upload route, no `request.files` usage, no attachment table in the clinic schema. Section 8 requirements (file/path safety) are therefore **NOT APPLICABLE** — there is nothing to validate.
- **No printing/export route** in `clinic_api.py` or `subsystem-clinic.js` (same conclusion as Retail's Phase 2B finding).
- **No distinct Clinic settings page/route** — confirmed again in Phase 3 (matches Phase 0: Clinic uses the shared generic settings surface, nothing Clinic-owned to extract).
- **Clinic-specific localization file does not exist** — Clinic UI strings are translated via the same shared `static/locales/{en,ar}.json` (139 keys) used by every subsystem, matched by an automatic DOM-text sweep in `i18n.js` (no explicit `t()` calls in `subsystem-clinic.js` at all). Some clinic-relevant strings ("Visits", "Lab Expenses", the standalone word "Clinic") are **not** present in that dictionary and fall back to English in Arabic mode — a pre-existing source gap, not introduced here (see `docs/migration/clinic-parity-matrix.md`).

## Known defects carried over from Phase 0's risk register (R3, R4, R5, R6 — clinic-specific)

1. **Stack trace returned to client** on `create_patient` failure (`clinic_api.py:106-108`, `'detail': traceback.format_exc()` in the JSON response) — a real information-disclosure issue, fixed in this phase with a test (see extraction report).
2. **`items_json` on prescriptions is `str()`-serialized, not real JSON** (`clinic_api.py:673-675`) — fixed in this phase with a test (see extraction report), since it is a genuine data-integrity defect with a clear, safe, backward-compatible fix (write real JSON; reading old `str()`-formatted rows would need `ast.literal_eval`, not touched since there is no legacy data to migrate in a fresh extraction).
3. **Hardcoded internal event-bus URL** (`clinic_api.py:29`, `http://127.0.0.1:5000/api/events/emit`) — made configurable via `AURA_EVENT_BUS_URL`, off by default, same treatment as Retail Phase 2.
4. **Cross-subsystem writes into Accounting** (`clinic_api.py:517,589`, lab-expense and invoice sync) — left in place unchanged. It is already wrapped in `try/except Exception` in source (best-effort, "never block the clinic action if accounting is down"). Since standalone Clinic has no Accounting subsystem, `from database.subsystem_db import ...` inside those blocks raises `ImportError`, caught by the existing `except`, and the clinic action completes normally — this is the source's own designed fallback behavior operating exactly as intended, not a new adaptation.
5c. **Seven create/link routes accepted a foreign-key id (`patient_id`, `visit_id`, `invoice_id`, `doctor_id`) from the request without verifying it belonged to the caller's own company.** Found by an automated security review of this extraction's own commits (not by the hand-written test suite, and not present in Phase 0's risk register — a genuinely new finding). Affected: `create_visit`, `add_note`, `add_followup`, `create_prescription`, `create_invoice`, `create_lab_expense`, `create_appointment`/`update_appointment`, and — most severely — `record_payment`, which had **no company_id filter on either its SELECT or its UPDATE**, meaning any authenticated user of any company could read another company's invoice total and mark it paid. This is a cross-tenant IDOR (insecure direct object reference) class of bug: an authenticated Company A user could attach clinical notes, follow-ups, prescriptions, invoices, or payments to a Company B patient/visit/invoice simply by guessing or incrementing an integer id. Fixed with a new `_owned(conn, table, row_id, cid)` helper called at the top of every affected route, returning 404 (not found — never confirms or denies existence of another company's record) when the referenced id doesn't belong to the caller's company. 9 new regression tests added (`clinic_rbac_test.py`, "IDOR regression" section). This was present in the ORIGINAL source `clinic_api.py` unchanged — extracting it verbatim carried the bug forward; it is not something this extraction introduced, but it is something this extraction is responsible for catching and fixing before shipping.
5b. **`create_patient` and `create_appointment` had no upfront validation of their NOT NULL fields, and no `try/finally` connection cleanup.** Discovered by the Phase 3 test suite itself: a test that intentionally omitted a required field (`name` / `patient_id`) triggered an unhandled `sqlite3.IntegrityError` mid-transaction. `create_appointment` had no exception handling at all (crashes propagate to Flask's default error handler); `create_patient` catches the exception (and, after the Phase 3 stack-trace fix, logs it safely) but never closed the already-open `conn` in the except branch. In both cases the leaked, still-open SQLite connection held a write lock that caused every *subsequent* request in the same process to fail with `database is locked` until the connection was eventually garbage-collected — a real availability bug a single malformed request could trigger. Fixed with upfront validation (`name`/`patient_id` required, clean 400 if missing) plus a defensive `conn.close()` in `create_patient`'s except branch. Not extended to every other route in the file (out of the narrow scope this discovery justified) — flagged as a class of risk worth a dedicated audit pass in a future phase.
5. **`demo-wipe`/`demo-seed` had no gate at all, and the wipe statements had no tenant scoping.** Discovered during Phase 3 extraction (not in Phase 0's risk register): the source `demo_wipe`/`demo_seed` routes were protected only by `@mt_login_required @mt_require_subsystem('clinic')` — no demo-mode check, no admin-only check, no confirmation token — and every `DELETE FROM clinic_*` statement in `demo_wipe` had **no `WHERE company_id=?` clause at all**, so it deleted every tenant's clinic data in the whole installation, not just the caller's own company. This is strictly worse than Retail's pre-Phase-1 state (which at least scoped by company, just lacked the mode/confirmation gates). Fixed in this phase: added `clinic_demo_mode_enabled()` (mirrors `retail_demo_mode_enabled()`), admin-only + confirmation-token checks, and full per-company scoping on every wipe statement (with a subquery for `clinic_invoice_items`, which has no `company_id` column of its own) — see `docs/security/clinic-rbac-matrix.md` and the extraction report. Also fixed in the same pass: `_seed_clinic` hardcoded `company_id=1` in every INSERT (same class of bug Retail's `_seed_retail` had before its own Phase 1 fix) — now parameterized.
