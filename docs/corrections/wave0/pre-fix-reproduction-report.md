# Wave 0 — Pre-Fix Reproduction Report

Baseline verified before any code change: tag `full-product-audit-phase3-5-complete`
confirmed present, branch `master`, working tree clean. Baseline tests run
isolated per file (matching the audit's documented strategy):

- Retail: `retail_import_export_test.py` 25 passed, `retail_localization_test.py`
  18 passed, `retail_pricing_test.py` 26 passed, `retail_security_test.py`
  47 passed → **116/116**.
- Clinic: `clinic_independence_test.py` 5, `clinic_localization_test.py` 13,
  `clinic_onboarding_auth_test.py` 15, `clinic_privacy_test.py` 9,
  `clinic_rbac_test.py` 24, `clinic_workflow_test.py` 29 → **95/95**.

Both match the audit's recorded baseline exactly.

Every defect below was reproduced live in this environment via
`.audit-temp/repro_wave0.py` (Retail) and `.audit-temp/repro_wave0_clinic.py`
(Clinic) — synthetic company/user/product/patient data only, temp SQLite
directories deleted immediately after each run, nothing committed.

## AUDIT-001 — Retail onboarding

- **Command**: `POST /api/onboarding/create-admin` against a freshly-booted
  Retail Flask test client (fresh temp `AURA_APP_DATA`, empty database).
- **Input**: `{name, email, password, company_name}`.
- **Expected result**: 200, first admin account created.
- **Actual result**: **404 Not Found.**
- **Affected route**: `POST /api/onboarding/create-admin` (never registered).
- **Affected file**: `products/retail/backend/app.py` (missing import/registration).
- **Affected platform**: Windows + Android (shared backend).
- **Database effect**: none — route unreachable, no write attempted.
- **Financial effect**: none directly; blocks all use of the product.
- **Evidence**: `docs/audit/12-retail-functional-audit.md` CRITICAL FINDING.

## AUDIT-002 / AUDIT-003 — Retail financial authority

- **Command**: `POST /api/sub/retail/sales` with an Android-style payload
  (no `tax_amount`/`discount_amount` fields — Kotlin defaults them to 0.0)
  against a product configured with `tax_rate=15`, `sell_price=100`.
- **Input**: `{subtotal: 100.0, total: 100.0, amount_paid: 100.0, items: [{product_id, quantity: 1, unit_price: 100.0}], idempotency_key}`.
- **Expected result**: server computes `tax=13.50`, `total=113.50` (after-discount
  default, zero discount here) from the product's own configured tax rate.
- **Actual result**: **persisted `discount_amount=0.0, tax_amount=0.0, total=100.0`**
  — the client-submitted figures were stored verbatim.
- **Second reproduction (tampered total)**: same $100 item, client submits
  `subtotal=1.0, total=1.0, amount_paid=1.0`. **Persisted `subtotal=1.0,
  total=1.0`** — the server accepted an arbitrary, self-inconsistent total
  with no cross-check against `unit_price * quantity`.
- **Affected route**: `POST /api/sub/retail/sales`.
- **Affected file**: `products/retail/backend/api/retail_api.py`, `create_sale()`.
- **Affected platform**: both (server-side gap; Android is the client that
  actually exercises the zero-tax path in practice, per `03`/`05`).
- **Database effect**: `sales.subtotal/discount_amount/tax_amount/total`,
  `sale_items.discount_pct/tax_rate/line_total` all client-controlled.
- **Financial effect**: proven under-collection of tax; proven arbitrary-total
  acceptance.
- **Evidence**: `docs/audit/03-retail-financial-audit.md`,
  `docs/audit/05-cross-platform-financial-parity.md`.

## AUDIT-004 — Retail returns

- **Command**: `POST /api/sub/retail/returns` with `sale_id=999999999`
  (nonexistent) and a 50-unit return of an item that was never sold.
- **Expected result**: rejected — no such sale.
- **Actual result**: **200, `refund_amount=5000.0` credited.**
- **Second identical submission**: rejected with a 500
  (`UNIQUE constraint failed: returns.return_number`) — but only because
  `return_number = f'RET-{int(time.time())}'` collided within the same
  second; this is an **accidental** side effect of the numbering scheme, not
  a real duplicate-submission guard (spacing the two requests a second apart
  would produce two independent refunds, as the audit's own analysis
  predicted).
- **Affected route**: `POST /api/sub/retail/returns`.
- **Affected file**: `products/retail/backend/api/retail_api.py`, `create_return()`.
- **Affected platform**: both.
- **Database effect**: `returns`, `return_items`, `inventory_balances` all
  written with no linkage validation.
- **Financial effect**: proven — an arbitrary refund was created against a
  sale that does not exist.
- **Evidence**: `docs/audit/03-retail-financial-audit.md`.

## AUDIT-011 — Clinic payment validation

- **Command**: three `POST /api/sub/clinic/payments` calls against a $100
  invoice: `amount=-10.0`, `amount=0.0`, `amount=9999.0`.
- **Expected result**: all three rejected (negative, zero, and
  overpayment-beyond-any-credit-policy respectively).
- **Actual result**: **all three accepted with 200**; the $9999 payment
  marked the invoice `'paid'` with no cap or overpayment tracking.
- **Affected route**: `POST /api/sub/clinic/payments`.
- **Affected file**: `products/clinic/backend/api/clinic_api.py`, `record_payment()`.
- **Evidence**: `docs/audit/04-clinic-financial-audit.md`.

## AUDIT-012 — Clinic payment idempotency

- **Command**: two identical `POST /api/sub/clinic/payments` calls,
  `amount=40.0` each, no idempotency key field exists on the route to
  de-duplicate with.
- **Expected result**: one payment recorded (if this were a genuine retry).
- **Actual result**: **both accepted independently** — `SUM(amount_paid)`
  across all five payments made in this reproduction run (including the
  invalid ones above) totalled `10069.0` against a $100 invoice, confirming
  every single call — valid or not — was persisted with no deduplication of
  any kind.
- **Evidence**: `docs/audit/04-clinic-financial-audit.md`.

## AUDIT-019 — Backup/restore

Not reproduced as a runtime failure — there is no code path to invoke,
confirmed by the Phase 3.5 repo-wide search (`docs/audit/18-backup-and-recovery-audit.md`).
Verified still true at the start of this phase by re-running the same search;
no backup/restore code exists anywhere in `commercial_runtime/`,
`products/*/backend`, or `products/*/desktop` prior to this phase's work.

## Cleanup

Both reproduction scripts deleted their temp `AURA_APP_DATA` directories on
completion (`shutil.rmtree`). No synthetic data or scratch database was left
on disk or committed. The two scripts themselves live in `.audit-temp/`
(git-ignored, not committed).
