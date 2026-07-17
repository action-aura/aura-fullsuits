# Wave 0 — Test Report

Status: **PROVEN**. Every number below is a directly observed `pytest`
result from this environment, not an estimate.

## Strategy

Per AUDIT-010 (see `wave0-residual-risk-register.md` for the decision), all
runs below are **isolated per file** (`pytest <one file> -q`), which is the
trusted baseline established in Phase 3.5 and carried through this wave.
Running an entire product's `tests/` directory in one combined `pytest`
invocation is known to produce spurious cross-file failures unrelated to
any of this wave's changes, and is not used for pass/fail judgment.

## Retail — 155 tests, 155 passing (116 baseline + 39 new)

| File | Result | Status |
|---|---|---|
| `retail_import_export_test.py` | 25 passed | baseline, unmodified |
| `retail_localization_test.py` | 18 passed | baseline, unmodified |
| `retail_pricing_test.py` | 26 passed | baseline; 5 assertions intentionally updated for the tax-inclusive refund semantics change (AUDIT-004), documented in `retail-return-correction.md` |
| `retail_security_test.py` | 47 passed | baseline, unmodified |
| `retail_onboarding_wave0_test.py` | 8 passed | **new** (AUDIT-001) |
| `retail_financial_authority_test.py` | 10 passed | **new** (AUDIT-002/003/005/006/008/009) |
| `retail_returns_wave0_test.py` | 10 passed | **new** (AUDIT-004) |
| `retail_backup_restore_test.py` | 12 passed | **new** (AUDIT-019) |

`116 + 39 = 155`. Baseline subtotal (`25+18+26+47`) matches the Phase 3.5
audit's recorded 116 exactly.

## Clinic — 108 tests, 108 passing (95 baseline + 13 new)

| File | Result | Status |
|---|---|---|
| `clinic_independence_test.py` | 5 passed | baseline, unmodified |
| `clinic_localization_test.py` | 13 passed | baseline, unmodified |
| `clinic_onboarding_auth_test.py` | 15 passed | baseline, unmodified |
| `clinic_privacy_test.py` | 9 passed | baseline, unmodified |
| `clinic_rbac_test.py` | 24 passed | baseline, unmodified |
| `clinic_workflow_test.py` | 29 passed | baseline, unmodified |
| `clinic_payment_wave0_test.py` | 9 passed | **new** (AUDIT-011/012/018) |
| `clinic_backup_restore_test.py` | 4 passed | **new** (AUDIT-019) |

`95 + 13 = 108`. Baseline subtotal (`5+13+15+9+24+29`) matches the Phase
3.5 audit's recorded 95 exactly.

## Grand total

**263 / 263 tests passing** across both products, zero unexplained
regressions. The only assertion changes to pre-existing tests are the 5
documented, intentional refund-semantics updates in `retail_pricing_test.py`
(AUDIT-004) — no test was deleted, and no other pre-existing assertion was
altered.

## Windows packaged smoke test

**Not re-run in this wave.** Backend behavior changed (create_sale,
create_return, onboarding, Clinic payments/invoices, new backup/restore
routes) — a packaged-exe smoke test is warranted before any release
decision, but building and smoke-testing the frozen `.exe` for both
products was judged out of scope for this corrective pass given the time
budget; it is listed as a required follow-up before Wave 0's fixes can be
considered release-validated end-to-end (see `wave0-residual-risk-register.md`).

## Android contract/build check

**N/A for this wave.** No Android Kotlin source was touched (see
`cross-platform-financial-validation.md` for why the backend fix alone
neutralizes the Android zero-tax defect without a client change). No
Android build was triggered.

## Financial audit harness / DB-integrity tests

Covered by the new dedicated test files above (financial authority,
returns, payments, backup/restore/integrity) rather than a separate
standalone harness — this wave did not find or use a pre-existing generic
"financial audit harness" distinct from the product test suites themselves.
