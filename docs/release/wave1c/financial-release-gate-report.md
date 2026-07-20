# Wave 1C -- Financial Release Gate Report (Part C)

## Method
Independent re-verification, not a re-read of prior reports. Two new audit-only test files were added and executed against the real, running backend services (in-process, same harness pattern as the existing suite) -- every assertion is against an actual server HTTP response and/or database row, never hand-derived math:
- `products/retail/tests/wave1c_financial_gate_test.py` -- 4/4 passed (28.97s)
- `products/clinic/tests/wave1c_financial_gate_test.py` -- 8/8 passed (38.03s)

These supplement, not replace, the existing baseline suites (`retail_financial_authority_test.py` 10/10, `retail_returns_wave0_test.py` 10/10, `clinic_payment_wave0_test.py` 9/9, all still passing per `automated-regression-report.md`).

## Retail results

| # | Case | Result | Evidence |
|---|---|---|---|
| 1 | Worked example: price 100.00, discount 20%, tax 10% | **PASS** | Server returned `subtotal=100.00, discount_amount=20.00, tax_amount=8.00, total=88.00`; (100-20)*1.10 = 88.00 |
| 2 | Manipulated client totals (`subtotal=1, tax_amount=0, total=1, unit_price=1, tax_rate=0` sent alongside real 20%/10% line) | **PASS** | Server ignored every client-submitted financial field; returned `total=88.00`, matching case 1 |
| 3 | Same `idempotency_key` posted 3x | **PASS** | All 3 responses returned the identical sale id; `COUNT(*) WHERE idempotency_key=?` = 1; stock decremented exactly once (10->8, not 4 or 6) |
| 4 | Full return, then a second return on the same sale; separately, an over-quantity return (qty 99 vs qty 1 sold) | **PASS** | First return: `refund_amount=220.00`, stock restored 8->10. Second return on the same sale: rejected 400 ("remain returnable"). Over-return: rejected 400. Stock confirmed not double/extra-restored in either case |

## Clinic results

| # | Case | Result | Evidence |
|---|---|---|---|
| 1 | Client-submitted `total=1/subtotal=1/tax=999` vs. real qty=2 @ $100 items | **PASS** | Server computed and persisted `total=200.00` -- the endpoint does not even read a client `total` field |
| 2 | Zero payment amount | **PASS** | 400 |
| 3 | Negative payment amount | **PASS** | 400 |
| 4 | Overpayment (70.00 against a 60.00 balance after a 40.00 partial payment) | **PASS** | 400 "outstanding"; DB confirms `total_paid` stayed 40.00 |
| 5 | Partial payment (35.00 on a 100.00 invoice) | **PASS** | `status='partial', amount_paid=35.00, outstanding=65.00` (DB-verified) |
| 6 | Exact settlement (40.00 + 60.00) | **PASS** | `status='paid', amount_paid=100.00` |
| 7 | Duplicate payment, same `idempotency_key`, posted 3x | **PASS** | 1 payment row, `total_paid=40.00` (not 80/120) |
| 8 | Transaction rollback: forced `RuntimeError` inside `record_payment`'s `_audit()` call, inside the same `BEGIN IMMEDIATE` transaction, after the payment INSERT/invoice UPDATE but before commit | **PASS** | 500 returned; invoice stayed `status='unpaid'/amount_paid=0`; zero orphaned `clinic_payments` rows; invoice remained cleanly payable afterward, proving no wedged state |

## Verdict
**All 12 required cases PASS.** No financial defect, of any severity, found in this re-audit. Server-side financial authority (Retail sales/returns, Clinic invoices/payments), idempotency collapse under retry, and transactional rollback safety are all independently confirmed against the live Wave 1B backend -- this is not a re-statement of the Wave 0 correction, it is a fresh, adversarial re-proof of it.

**Retail financial gate: PASS.**
**Clinic financial gate: PASS.**

No unresolved normal-use financial P0/P1 exists for either product. This clears the financial-correctness requirement for Gate 2 (Controlled Pilot) and Gate 3 (Controlled Paid Pilot) for both products.

## Note on test harness addition
Case 8 (Clinic rollback) required a fault-injection seam not present in any existing test file (`monkeypatch` on `api.clinic_api._audit`, restored in a `finally` block) -- a minimal, non-invasive swap of a module-level function reference for the duration of one test. No production code was modified to enable this test.
