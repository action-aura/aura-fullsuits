# Aura Clinic — Final Parity Matrix (Phase 3)

Statuses: **PASS** / **PASS WITH DOCUMENTED LIMITATION** / **FAIL** / **NOT PRESENT IN SOURCE** / **NOT APPLICABLE** / **DEFERRED TO PHASE 4 ANDROID**

| Area | Status | Notes |
|---|---|---|
| Onboarding | PASS | First-run wizard (`/api/onboarding/status`, `/create-admin`, `/complete`) extracted from `api/standalone_auth.py`, verified end-to-end including against the packaged exe (clean DB → onboarding → first admin → login → restart-persists). No hardcoded credential anywhere. |
| Login/logout | PASS | Shared `commercial_runtime/identity/auth_routes.py`, unchanged from Phase 2. |
| Dashboard | PASS | `dashboard_stats` ported verbatim; verified shape + counts by test and packaged smoke test. |
| Patients | PASS | Create/edit/search/soft-archive/hard-delete (admin-only, blocked by billing history) all ported verbatim and verified. |
| Patient profile | PASS | `GET /patients/<id>` returns patient + visits + appointments in one payload, matches source. Overview/Visits/Prescriptions/Invoices/Notes are UI tabs over these same API responses — no separate backend routes exist per tab in source (confirmed), so there is nothing additional to extract per sub-tab. |
| Visits | PASS | Create/view/update (doctor-gated diagnosis+treatment)/clinical notes (doctor-gated) all ported and verified; appointment→visit status linkage verified. |
| Prescriptions | PASS WITH DOCUMENTED LIMITATION | Create (doctor-gated)/history ported and verified. `items_json` storage format fixed (str-repr → real JSON, Phase 3 corrective fix, see extraction report) — a data-integrity improvement, not a feature change; medication rows/dosage fields are whatever the caller puts in the `items` array (source has no fixed sub-schema for prescription line items, confirmed — not invented here either). |
| Invoices | PASS | Create/list/detail ported verbatim; discount-before-tax computation, discount-clamped-to-subtotal, monetary precision all verified by test. |
| Payments | PASS | Record payment + invoice status transition (unpaid→partial→paid) ported verbatim and verified. |
| Notes | PASS | Clinical visit notes (doctor-gated) verified. No separate "internal notes" or attachment feature exists in source beyond visit notes and the patient `notes` field (confirmed by inspection). |
| Appointments | PASS | Booking, double-booking conflict (409), check-in, search-across-dates all ported and verified. Invalid-appointment handling (missing `patient_id`) was a genuine gap in source (unhandled `IntegrityError` + connection leak) — fixed with input validation, documented as a corrective fix, not a silent behavior change to valid requests. |
| Doctors | PASS | CRUD + specialty field ported and verified. |
| Lab expenses | PASS | Entry/deletion/monetary precision verified; best-effort Accounting sync confirmed to fail soft (as designed) without blocking the clinic record. |
| Settings | NOT PRESENT IN SOURCE | No Clinic-specific settings route/page exists — confirmed again in Phase 3 (matches Phase 0's finding). Clinic uses the shared generic settings surface, nothing Clinic-owned to extract. |
| Documents | NOT APPLICABLE | No document/attachment upload feature exists anywhere in source Clinic (confirmed: zero `request.files` usage in `clinic_api.py`). Section 8 of the task (file/path safety) has nothing to validate. |
| Authentication | PASS | Shared with Retail, unchanged; verified via onboarding + direct-provisioned accounts, disabled-account rejection, session cookie hardening, bounded session lifetime. |
| Authorization | PASS | `mt_require_subsystem('clinic')` (license + per-user access level) verified; tested for both admin bypass and non-admin denial without an explicit permission grant. |
| Role enforcement | PASS | Real binary model (admin / doctor / everyone-else) verified route-by-route — see `docs/security/clinic-rbac-matrix.md`. Secretary blocked from all 5 doctor-only routes; doctor blocked from all admin-only routes; role promotion takes effect on next login; disabled account rejected on its very next request (not just next login); cross-company isolation verified, including on the newly-hardened demo-wipe and the cross-tenant IDOR fix across 8 create/link routes (found by automated security review, fixed, 9 regression tests, reverified against the rebuilt packaged exe). |
| Database | PASS | 13-table schema byte-identical to source; clean initialization (zero patient/appointment/visit/prescription/invoice/payment/doctor records) verified; foreign keys, uniqueness constraints, `PRAGMA foreign_keys=ON` all verified by test. |
| Localization | PASS WITH DOCUMENTED LIMITATION | Shares Retail's 139-key dictionary (no dedicated Clinic locale file in source); 139/139 keys present in both languages, zero untranslated pairs; 3 clinic-relevant strings ("Lab Expenses", "Visits", "Clinic") are absent from the source dictionary and fall back to English in Arabic mode — pre-existing gap, pinned by a regression-marker test, not introduced by this extraction. |
| RTL | PASS | Verified via the shared `i18n.js` loader's static behavior (`dir="rtl"` when Arabic active) — same loader Retail already uses, same verification method (no browser automation tool available in this stack for a live rendered check). |
| Offline operation | PASS | SQLite-backed, loopback-only architecture inherited unchanged; no outbound network calls except the now-configurable, off-by-default `AURA_EVENT_BUS_URL`. |
| Desktop entrypoint | PASS | `products/clinic/desktop/launcher_clinic.py` (new file, patterned on Retail's already-validated launcher) — verified end-to-end against the real packaged exe: single-instance guard, free-port scan, waitress start, clean shutdown, restart-persistence. |
| Windows packaging | PASS | Real PyInstaller build succeeded on the first attempt (benefited from Retail's Phase 2B bug-fix being applied from day one). Full 13-step packaged smoke test passed, including the onboarding wizard end-to-end and a restart-persistence check Retail's own smoke test didn't cover. Raw onedir build only — no signing/icon/installer, same posture as Retail. |
| Android source | DEFERRED TO PHASE 4 ANDROID | Confirmed present in source (Phase 0 inventory: `PatientsScreen.kt`, `PatientDetailScreen.kt`, `AppointmentsScreen.kt`, `ClinicExtraScreens.kt`, `clinic` Gradle flavor). Not copied, edited, or built in this phase — explicitly out of scope per the task. Not missing, not broken — scheduled work. |
| Privacy protections | PASS | See `docs/privacy/clinic-sensitive-data-boundary.md` — stack-trace-to-client leak fixed (corrective, tested); audit log confirmed ID-based not content-based; no telemetry/export/diagnostics feature exists yet to leak through (boundary documented for when one is built); debug mode confirmed off; no file-upload surface to validate (not applicable). |

## Summary

26 rows total: 21 PASS, 2 PASS WITH DOCUMENTED LIMITATION (Prescriptions, Localization — cosmetic/data-format notes, not functional gaps), 0 FAIL, 1 NOT PRESENT IN SOURCE (Settings), 1 NOT APPLICABLE (Documents), 1 DEFERRED TO PHASE 4 ANDROID. No unexplained FAIL anywhere in this matrix.
