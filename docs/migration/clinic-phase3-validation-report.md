# Aura Clinic — Phase 3 Validation Report

Companion to `clinic-extraction-report.md`, `clinic-parity-matrix.md`, `docs/security/clinic-rbac-matrix.md`, `docs/privacy/clinic-sensitive-data-boundary.md`, and `docs/build/clinic-windows-build-report.md`. No number below is estimated — every figure comes from an actual command run in this session.

## 1. Extraction — CLOSED

Clinic backend (`clinic_api.py`, 13-table schema), frontend (`subsystem-clinic.js`, shared locale files), and the onboarding/admin-management surface (newly extracted into shared `commercial_runtime`) are all in place. Not rebuilt from scratch — copied/extracted with adapted imports, per the task's explicit instruction.

## 2. Discovered and fixed defects (6, each with a test)

1. Stack trace returned to client on patient-creation failure (privacy).
2. Prescription `items_json` stored as Python repr instead of real JSON (data integrity).
3. Hardcoded internal event-bus URL (now configurable, off by default).
4. `demo-wipe`/`demo-seed`: no mode gate, no admin check, no confirmation token, **and no tenant scoping at all** (deleted every company's data) — found by manual inspection.
5. `create_patient`/`create_appointment`: missing NOT-NULL validation led to unhandled `IntegrityError`s that leaked open SQLite connections, locking the database for subsequent requests — found by the test suite itself, not by inspection.
6. **Cross-tenant IDOR across 8 routes** (`create_visit`, `add_note`, `add_followup`, `create_prescription`, `create_invoice`, `create_lab_expense`, `create_appointment`/`update_appointment`, `record_payment`) — none verified a foreign-key id (patient/visit/invoice/doctor) belonged to the caller's own company before using it. `record_payment` had **no company_id filter at all** on its read or write. This is the most severe finding of this phase — found by an **automated security review of this phase's own commits**, after the rest of the extraction was believed complete. Fixed with a new `_owned()` ownership-check helper; 9 regression tests added.

All six are documented in `clinic-extraction-report.md` with before/after behavior and the specific test that proves the fix. Finding 6 was caught late enough that it required rebuilding and re-verifying the Windows package after the initial packaged smoke test had already passed — see §6.

## 3. Test totals — Clinic

Run as 6 separate `pytest` invocations (matching the convention already established for Retail — each file's own bootstrap sets its own temp `AURA_APP_DATA`, and Python's module-caching across files in one process causes cross-file state bleed if batched, as documented in-line in `clinic_independence_test.py`).

| Suite | Tests | Passed | Failed | Skipped | Warnings |
|---|---|---|---|---|---|
| `clinic_onboarding_auth_test.py` | 15 | 15 | 0 | 0 | 0 |
| `clinic_rbac_test.py` | 15 | 15 | 0 | 0 | 0 |
| `clinic_workflow_test.py` | 29 | 29 | 0 | 0 | 0 |
| `clinic_privacy_test.py` | 9 | 9 | 0 | 0 | 0 |
| `clinic_localization_test.py` | 13 | 13 | 0 | 0 | 0 |
| `clinic_independence_test.py` | 5 | 5 | 0 | 0 | 0 |
| **Total** | **95** | **95** | **0** | **0** | **0** |

Re-run a second time with `PYTHONPATH` explicitly emptied before each invocation (independence verification, see §5) — identical result, 95/95 passing.

## 4. Retail regression totals (commercial_runtime WAS modified — full rerun mandatory)

`commercial_runtime/identity/registry_db.py` (2 new tables, additive), `commercial_runtime/identity/onboarding_routes.py` (new file, not registered in Retail's `app.py`), `commercial_runtime/security/modes.py` (1 new function, additive), `commercial_runtime/identity/auth_routes.py` (docstring only) were changed. No file inside `products/retail/` was touched.

| Suite | Tests | Passed | Failed |
|---|---|---|---|
| `retail_pricing_test.py` | 26 | 26 | 0 |
| `retail_security_test.py` | 47 | 47 | 0 |
| `retail_import_export_test.py` | 25 | 25 | 0 |
| `retail_localization_test.py` | 18 | 18 | 0 |
| **Total** | **116** | **116** | **0** |

**Retail remains fully unaffected.**

## 5. Independence verification

Static scan: zero references anywhere in `products/clinic` or the modified `commercial_runtime` files to the source repository's path or its pre-extraction `api.*`/`core.*`/`database.*` module paths (one deliberate, documented exception: the best-effort Accounting cross-import in `clinic_api.py`, which is *intended* to fail — see dependency map). Confirmed no `products/retail` import exists in Clinic's backend, and confirmed (regression check) no `products/clinic` import was introduced into Retail's backend.

Dynamic: all 95 Clinic tests pass with `PYTHONPATH` explicitly set to empty beforehand (§3, second run) — no accidental import could have reached the source repository under any reachable import-resolution order.

As in Phase 2B, the task's requested physical source-folder rename was not attempted — the source repository's uncommitted state was not re-verified as a precondition for a destructive action against the user's primary active repo, and the static+dynamic method above is offered as the substitute (same reasoning as documented in `retail-phase-2b-validation-report.md` §4; not repeated in full here).

## 6. Windows packaging

**Build: SUCCEEDED** (first attempt — no packaging bug found, since Clinic's `app.py` was written with Retail's Phase 2B fix already applied). **Packaged smoke test: PASSED**, all 13 mandated steps including a full onboarding-wizard walkthrough and a restart-persistence check.

The IDOR fix (§2 item 6) was found and applied **after** that first packaged build had already passed its smoke test, so the build was **rebuilt from the fixed source and re-verified** before this report was finalized — see the "Post-fix rebuild" addendum in `docs/build/clinic-windows-build-report.md` for the exact re-verification performed.

## 7. Definition of Done — checked against the task's 20 items

1. Extracted, not rebuilt — YES (§1).
2. Runs independently of source repo — YES (§5).
3. Clean onboarding works — YES, verified by test and by the packaged exe.
4. No hardcoded account/demo login — YES, verified by static scan (`test_no_hardcoded_credentials_anywhere_in_extracted_backend`) and by the fact that `create_admin` is the only account-creation path with no bypass.
5. Clean database initialization works — YES, verified (zero patient/appointment/visit/prescription/invoice/payment/doctor records on fresh install).
6. Core Clinic workflows preserved — YES (§ parity matrix, 21/26 rows PASS outright).
7. Admin/Doctor/Secretary permissions enforced server-side — YES, route-level, verified by test (RBAC matrix + `clinic_rbac_test.py`).
8. Patient data excluded from logs/generic diagnostics — YES, the one real leak found (stack trace) was fixed; audit log confirmed ID-based not content-based.
9. English/Arabic verified where supported — YES, with the pre-existing 3-key gap explicitly documented, not silently broken.
10. File paths/attachment storage safe — NOT APPLICABLE, no such feature exists in source (confirmed, not assumed).
11. All available Clinic tests ported — N/A, none existed in source (Phase 0 confirmed); 95 new tests authored instead, explicitly not presented as "ported."
12. New parity/security/privacy tests pass — YES, 95/95.
13. Real Windows package built and smoke-tested — YES.
14. N/A (Windows does exist).
15. Android untouched, deferred to Phase 4 — YES, confirmed not copied/edited/built.
16. Retail unaffected — YES (§4).
17. No unexplained FAIL in the parity matrix — YES (0 FAIL rows).
18. All limitations explicit — YES (extraction report §"Unresolved limitations").
19. Git checkpoint tag exists — see final commit/tag list in this session's closing summary.
20. Original AuraEnterprise repository untouched — YES, only read from throughout this phase; no write, rename, move, or delete performed against it.

## 8. Recommendation

Both Retail and Clinic are now extracted, hardened, tested, and packaged to the same standard. The natural next phases per the original master plan are Android (Phase 4, explicitly deferred here) or the Owner Control Center — both are large, independently-scoped efforts and neither is blocked by anything found in this phase.
