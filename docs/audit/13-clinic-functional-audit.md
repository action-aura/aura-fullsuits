# Aura Clinic — Functional Audit

Same status legend as `12`.

| Feature | Windows | Android | Notes |
|---|---|---|---|
| Onboarding / account creation | **PASS** | **PASS** | `onboarding_bp` correctly registered in `products/clinic/backend/app.py` (confirmed by direct read, contrasting with Retail's absence — see `12`); real smoke-tested end to end in Phase 3 (`docs/build/clinic-windows-build-report.md`, steps 2-4, admin creation via `POST /api/onboarding/create-admin`, PASS) |
| Login/logout | PASS | PASS | Shared `commercial_runtime` auth |
| Dashboard | PASS | PASS | Server-computed, `clinic_workflow_test.py::test_dashboard_stats_shape_and_counts` PASS |
| Patients (list/search/profile) | PASS | PASS | `_owned()`-scoped, IDOR-fixed (`07`/`11`) |
| Patient overview / visit history | PASS, **with the over-broad read-access finding** | Same (shared backend) | Any authenticated role can read full visit history via `GET /patients/<id>` — see `11` |
| Visits | PASS (create/complete gated to doctor for the clinical-content routes) | Same | `require_clinic_role('doctor')` on visit-completion (`clinic_api.py:428`) |
| Prescriptions | PASS (doctor-only write, `clinic_api.py:815`) | Same | Also fixed in Phase 3: prescription `items` now stored as real `json.dumps()` output instead of Python's `str(list)` repr (single-quote corruption bug for medication names with apostrophes) |
| Notes | PASS (doctor-only write, `clinic_api.py:451`) | Same | |
| Appointments (booking/edit/cancel) | PASS | PASS | `_owned()`-scoped |
| Doctors | PASS (doctor-only management, `clinic_api.py:519`) | Same | |
| Billing / invoices | **PASS, server-computed correctly** (`04`) | Same (shared backend) | Best financial-integrity design of either product's write paths |
| Payments | **PASS with validation gaps** (`04`) | Same | No amount validation, no idempotency (`04`) |
| Lab expenses | PASS (create/delete tested, `clinic_workflow_test.py`) | Same | |
| Settings | PASS | PASS | |
| User management / roles | PASS, **with the read-access scope finding** | Same | `require_clinic_role('doctor')` correctly gates clinical writes; no equivalent gate exists on clinical reads (`11`) |
| Documents | **NOT PRESENT** — no document/file-upload route was found in `clinic_api.py` in the sections read | Same | Listed in the audit's own "at minimum" checklist as an area to check; not located, treated as NOT PRESENT rather than defective |
| Localization (English/Arabic) | PASS | PASS | `clinic_localization_test.py` 13/13 pass isolated |
| RTL | PASS (structural) | BUILD ONLY / REQUIRES PHYSICAL DEVICE VALIDATION | Same caveat as Retail (`12`) |
| Theme | PASS | PASS | |
| Navigation (Android: patient-detail back-stack) | N/A | PASS | `AppRoot.kt`'s `isDetail` check for `patient/{id}` routes, confirmed present in Phase 4 migration |
| Offline operation | PASS by architecture | PASS by architecture | Same as Retail |
| Startup/shutdown | **PASS — real 13-step smoke test** | BUILD ONLY (no device) | `docs/build/clinic-windows-build-report.md` — the most thoroughly real-smoke-tested workflow of either product |
| Restart persistence | **PASS — real smoke test, including secret-key reuse and `needs_setup:false` on second launch** | UNVERIFIED (no device) | Same source |
| Large database behavior | UNVERIFIED | UNVERIFIED | See `17` |

## Cross-subsystem integration note (new finding, see `04`)

`create_invoice()` contains a "mirror this invoice into Accounting" code path
that always fails and is always silently swallowed, because
`database/subsystem_db.py`/`get_accounting_conn` do not exist anywhere in the
extracted `aura-fullsuits` tree — a leftover reference to the original
monolith's cross-subsystem wiring. Not a functional defect for Clinic itself
(the clinic invoice is written correctly regardless), but the "linked to both
invoice systems" behavior described in the code's own comment does not
actually occur in this product. See `04` for full detail.

## Commercial impact summary

Clinic is materially more complete and more carefully hardened than Retail on
every axis this audit examined: it has a working onboarding flow (Retail does
not — see `12`), server-side financial computation with input clamping
(Retail trusts the client — see `03`), and a real, diff-verified IDOR fix with
regression coverage (`clinic_privacy_test.py`, `clinic_rbac_test.py`). Its
remaining gaps — payment validation/idempotency (`04`), over-broad clinical
read access by non-doctor roles (`11`), and the dead-code Accounting-mirror
call (`04`) — are all real but each individually less severe than Retail's
onboarding gap or Android tax omission. If forced to rank "which product is
closer to a first paid customer," the evidence in this audit points to
**Clinic**, not Retail — see `24` for the full scored verdict.
