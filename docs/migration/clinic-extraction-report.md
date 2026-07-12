# Aura Clinic Extraction Report (Phase 3)

Companion to `clinic-source-inventory.md`, `clinic-dependency-map.md`, `clinic-parity-matrix.md`, `docs/security/clinic-rbac-matrix.md`, `docs/privacy/clinic-sensitive-data-boundary.md`, and `docs/migration/clinic-phase3-validation-report.md`.

## Result summary

`products/clinic/` is now an independently runnable Flask backend + frontend for Aura Clinic, requiring only `commercial_runtime/` (extended in this phase — additive only, see below) from the rest of `aura-fullsuits`. No dependency on Action Aura Enterprise's `core/`, `api/`, or `database/` packages, and no dependency on `products/retail/`.

## Copied files (byte-identical business logic where unmodified, adapted imports)

| File | Source | Destination | Change |
|---|---|---|---|
| Clinic API routes | `api/subsystems/clinic_api.py` (768 lines) | `products/clinic/backend/api/clinic_api.py` | Import paths adapted; 5 documented corrective fixes (see below); all other route logic and SQL untouched. |
| Clinic web UI | `static/js/subsystem-clinic.js` (1448 lines) | `products/clinic/frontend/subsystem-clinic.js` | Byte-identical copy. |
| Locale files + loader | `static/locales/{en,ar}.json`, `static/js/i18n.js` | `products/clinic/frontend/{locales/,i18n.js}` | Byte-identical copy, duplicated from `products/retail/frontend/` (matches the Phase 2 precedent for `import-wizard.js`). |

## Surgically extracted (function-level, from shared multi-subsystem files)

| Extracted | Source | Destination |
|---|---|---|
| `init_clinic`, `_seed_clinic` (13-table schema) | `database/subsystem_db.py:5109-5390` | `products/clinic/backend/database/schema.py` |
| Onboarding + admin employee management (11 routes) | `api/standalone_auth.py` | `commercial_runtime/identity/onboarding_routes.py` (new file, shared with Retail — see "Shared commercial_runtime changes" below) |

## Shared commercial_runtime code reused unchanged from Phase 2

`mt_login_required`, `mt_require_subsystem`, `require_clinic_role`, `authenticate_registry_user`, `create_session`, password hashing, audit, per-install secret key, `users`/`company_modules`/`user_permissions`/`audit_logs` tables, login/logout/language-persistence routes. `require_clinic_role` was ported in Phase 2 even though Retail never used it — built for exactly this moment.

## Shared commercial_runtime changes (Phase 3 — additive only)

Per the task's constraint ("Do not modify the validated Retail implementation unless a genuinely shared commercial_runtime defect is discovered... strictly backward compatible, covered by Retail regression tests, documented clearly, committed separately"):

1. **`commercial_runtime/identity/registry_db.py`**: added `secure_links` and `company_settings` tables (additive `CREATE TABLE IF NOT EXISTS` — does not touch any existing table Retail uses).
2. **`commercial_runtime/identity/onboarding_routes.py`** (new file): the onboarding wizard + admin employee-management surface, extracted from `api/standalone_auth.py`. Registered in Clinic's `app.py`. **Not registered in Retail's `app.py`** — Retail was already flagged in the Phase 2B report as missing this surface, and the task's Retail-freeze instruction means that gap is deliberately left open rather than retrofitted as a side effect of Clinic work. Retail's `products/retail/backend/app.py` was not touched.
3. **`commercial_runtime/security/modes.py`**: added `clinic_demo_mode_enabled()` alongside the existing `retail_demo_mode_enabled()` — same function shape, new env var (`AURA_CLINIC_DEMO_MODE`), zero change to the existing Retail function.
4. **`commercial_runtime/identity/auth_routes.py`**: docstring comment updated only (no behavior change) to reflect that the onboarding surface now exists.

**Retail regression**: all 116 Retail tests were rerun after these changes (Retail source files themselves were not touched) — see `clinic-phase3-validation-report.md` for the actual result.

## Documented corrective fixes (each with a test, per the task's explicit allowance for genuine defects)

1. **Stack trace returned to client** (`create_patient`, `clinic_api.py:106-108` in source) — removed; now logs server-side only. Privacy-relevant (a traceback from a failed patient INSERT can echo bound field values). Test: `clinic_privacy_test.py::test_patient_creation_error_does_not_leak_traceback_to_client`.
2. **`prescriptions.items_json` written as Python `str()` repr instead of real JSON** — fixed to `json.dumps()`. Confirmed backward compatible with the frontend's existing `.replace(/'/g,'"')` workaround (a no-op against real JSON, which has no single quotes) and fixes the actual corruption case (medication names containing an apostrophe). Test: `clinic_workflow_test.py::test_prescription_creation_stores_real_json`.
3. **Hardcoded internal event-bus URL** (`http://127.0.0.1:5000/api/events/emit`) — made configurable via `AURA_EVENT_BUS_URL`, off by default, same treatment as Retail Phase 2.
4. **`demo-wipe`/`demo-seed` had no mode gate, no admin check, no confirmation token, and the wipe statements had no `WHERE company_id=?` at all** (deleted every tenant's clinic data, not just the caller's) — this is a materially worse gap than anything Retail had pre-hardening. Fixed with the exact pattern already proven in Retail's own Phase 1 remediation: `clinic_demo_mode_enabled()` gate, admin-only check, per-company confirmation token, and full company-scoping (with a subquery for `clinic_invoice_items`, which has no `company_id` column). `_seed_clinic` was also fixed to accept a real `company_id` parameter instead of hardcoding `1` (same class of bug Retail's `_seed_retail` had before its own Phase 1 fix). Tests: `clinic_rbac_test.py`'s 4 demo-wipe tests.
5. **`create_patient` and `create_appointment` had no upfront validation of NOT NULL fields, and leaked an open SQLite connection on failure** — discovered by the Phase 3 test suite itself (a test with a deliberately missing required field triggered a real `database is locked` cascade against subsequent requests). Fixed with upfront validation (clean 400 instead of an unhandled `IntegrityError`) plus defensive connection cleanup in `create_patient`'s except branch. See source inventory item 5b for the full incident description.

6. **Cross-tenant IDOR (insecure direct object reference) across 8 routes** — found by an automated security review of this phase's own commits, after the rest of the extraction was already complete. `create_visit`, `add_note`, `add_followup`, `create_prescription`, `create_invoice`, `create_lab_expense`, `create_appointment`/`update_appointment`, and `record_payment` all accepted a foreign-key id (`patient_id`/`visit_id`/`invoice_id`/`doctor_id`) from the request body or URL without verifying it belonged to the caller's own company — present in source unchanged, carried forward by the verbatim extraction. `record_payment` was the most severe: its SELECT and UPDATE against `clinic_invoices` had **no `company_id` filter at all**, so any authenticated user of any company could read another company's invoice total and mark it paid. Fixed with a new `_owned(conn, table, row_id, cid)` helper, called at the top of every affected route, returning 404 when the referenced id doesn't belong to the caller's company (never confirms or denies existence of another company's record). 9 regression tests added to `clinic_rbac_test.py`. See `docs/migration/clinic-source-inventory.md` item 5c and `docs/security/clinic-rbac-matrix.md`.

## Cross-subsystem coupling — left in place, unresolved on purpose

The lab-expense and invoice best-effort sync into Accounting (`clinic_api.py:517,589` in source) is untouched: it's already wrapped in `try/except Exception` in source, and since standalone Clinic has no `database.subsystem_db`/Accounting module, the `from database.subsystem_db import ...` inside those blocks raises `ImportError`, caught by the existing except, and the clinic action completes normally. This is the source's own designed fallback operating exactly as intended — verified by test (`clinic_workflow_test.py::test_lab_expense_accounting_sync_failure_does_not_block_the_clinic_record`).

## Database status

13-table schema, byte-identical to source. Clean initialization verified: zero patients/appointments/visits/prescriptions/invoices/payments/doctors on a fresh install (enforced by construction — `_seed_clinic` only runs when `not _is_standalone()`, and every packaged/test build sets `AURA_STANDALONE=1`). Foreign keys, uniqueness constraints (`patient_code`, `invoice_number`), and `PRAGMA foreign_keys=ON` all verified by test.

## Localization status

Clinic has no dedicated locale file in source — it shares the same 139-key `static/locales/{en,ar}.json` used platform-wide, matched entirely via `i18n.js`'s automatic DOM-text-sweep (zero explicit `t()`/`data-i18n` calls anywhere in `subsystem-clinic.js`, confirmed by inspection). 139/139 keys present in both languages, zero untranslated (identical en==ar) pairs. Three clinic-relevant strings ("Lab Expenses", "Visits", the standalone word "Clinic") are absent from the dictionary in source and fall back to English in Arabic mode — a pre-existing gap, not introduced here, pinned by a regression-marker test so it's easy to notice if it's ever fixed.

## Test status

See `docs/migration/clinic-phase3-validation-report.md` for actual totals (never estimated).

## Packaging status

See `docs/build/clinic-windows-build-report.md`.

## Unresolved limitations

1. No HTML host template for the Clinic web UI (same gap as Retail — confirmed, not newly introduced).
2. No export or printing feature exists in source Clinic (confirmed by inspection — nothing to extract).
3. No document/attachment upload feature exists in source Clinic (confirmed — Section 8 of the task is Not Applicable).
4. The richer 7-role RBAC catalog (`core/rbac/roles.py`'s `clinic` dict) remains unwired, matching source — not retrofitted.
5. `session_version` is bumped on role/status changes but never actually compared anywhere in the login-check decorators — a genuine pre-existing gap in the shared `commercial_runtime/identity/mt_auth.py`, documented in the RBAC matrix, not fixed in this phase (fixing it would change Retail's authentication behavior too, which is out of scope here).
6. The connection-leak fix (item 5 above) was applied narrowly to the two routes a real test caught failing — not audited across every route in `clinic_api.py`. Flagged as a class of risk for a dedicated future pass.
7. Android untouched, cleanly deferred to Phase 4 (not migrated, not built, not claimed complete).
