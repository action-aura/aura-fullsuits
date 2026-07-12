# Aura Clinic — Dependency Map (Phase 3)

## Imports `clinic_api.py` makes, and where they resolve after extraction

| Source import | Resolves to (in `aura-fullsuits`) |
|---|---|
| `from api.mt_auth import mt_login_required, mt_require_subsystem, require_clinic_role` | `from commercial_runtime.identity.mt_auth import ...` |
| `from database.subsystem_db import get_clinic_conn, sub_create, init_clinic` | `from database.schema import get_clinic_conn, sub_create, init_clinic` (local to `products/clinic/backend/`) |
| `from database.subsystem_db import sub_create, get_accounting_conn` (inside lab-expense/invoice handlers) | Left unresolved on purpose — see source inventory "Known defects" #4. Caught by the existing `except Exception`. |

No other top-level imports in `clinic_api.py`. Unlike `retail_api.py`, Clinic has **no local pricing/tax-engine module** to extract (tax on an invoice is a flat `tax_rate` param, computed inline in `create_invoice` — no separate `core/clinic/` package exists in source).

## Database dependency

`clinic_api.py` touches exactly 13 tables, all defined in `init_clinic()`: `clinic_patients`, `clinic_appointments`, `clinic_doctors`, `clinic_visits`, `clinic_visit_notes`, `clinic_services`, `clinic_invoices`, `clinic_invoice_items`, `clinic_payments`, `clinic_prescriptions`, `clinic_audit_log`, `clinic_followups`, `clinic_lab_expenses`. All are self-contained to the `clinic` SQLite database (`database/subsystems/clinic.db` in source, `products/clinic/backend`-relative `database/subsystems/clinic.db` here) — no foreign keys reach outside this table set except the best-effort Accounting sync (already addressed).

## Shared identity/auth dependency (same foundation Retail uses)

`commercial_runtime/identity/mt_auth.py`, `commercial_runtime/identity/registry_db.py`, `commercial_runtime/identity/auth_routes.py`, `commercial_runtime/security/*` — all reused unchanged from Phase 2, with `registry_db.py` and `auth_routes.py` **extended** (additive only) in Phase 3 to carry the onboarding/admin-employee surface both products need. See source inventory for the exact new routes/tables.

## Frontend dependency

`static/js/subsystem-clinic.js` depends on:
- The generic `SubsystemApp` shell (same undetermined shared-shell dependency flagged for Retail in Phase 2 — still not located/extracted; Clinic has the identical gap, not a new one).
- `static/locales/{en,ar}.json` + `static/js/i18n.js` — same shared files already extracted for Retail (`products/retail/frontend/`), duplicated into `products/clinic/frontend/` rather than shared via `commercial_runtime` (matches the Phase 2 precedent set for `import-wizard.js`: duplicate now, dedupe later if a real need arises — YAGNI).

## Explicitly NOT needed (confirmed zero references from `clinic_api.py` or `subsystem-clinic.js`)

`core/accounting/*` (only reached via the best-effort, now-inert sync calls), `core/hr/*`, `core/crm/*`, `core/ai/*`, `core/lifecycle/*`, `core/discovery/*`, `core/registry_system/*`, `core/plugin_manager/*`, `core/rbac/engine.py`, `core/rbac/permissions.py` (roles.py's clinic dict is documentation-only, not imported by any Clinic code path), `database/db_manager.py`, `database/registry_db.py`'s non-`users`/`company_modules`/`user_permissions`/`audit_logs`/`secure_links`/`company_settings` tables (EIP module catalog, document flow, numbering sequences, custom fields — all platform-wide, Clinic touches none of them).

## Dependency direction rule (unchanged from Phase 2)

Clinic must not import Retail, and does not (confirmed: no `products.retail` or `products/retail` reference anywhere in the extracted Clinic tree — see independence verification in the Phase 3 validation report). Both depend only on `commercial_runtime`.
