# Phase 4D/4H — Cross-Platform Financial Contract Report

Status: **PROVEN** at the source/unit-test level; **NOT device-verified**
(no emulator/device available — see `android-device-testing-guide.md`).

## Retail worked example (Phase 4D's explicit synthetic case)

Product: authoritative price 100.00, discount 20.00 (20%), tax rate 10%.

**Expected backend response** (per
`docs/architecture/financial-authority-contracts.md`): subtotal 100.00,
discount 20.00, taxable base 80.00, tax 8.00, **total 88.00**.

**Manipulated Android payload** containing `tax=0, total=80, subtotal=1,
price=1` must not alter the persisted result.

### Proof this holds

1. **The request cannot even carry those fields.** `CreateSaleRequest`
   (Kotlin) has no `subtotal`/`discount_amount`/`tax_amount`/`total`
   fields at all as of this phase's fix — `SaleContractTest.kt`'s
   `createSaleRequest_carries_no_client_computed_totals` and
   `createSaleRequest_serializes_only_commercial_intent_json_keys` prove
   this by reflection and by actual Gson serialization respectively.
2. **The backend ignores them even if a raw HTTP client sent them
   anyway** — unchanged from Wave 0 (`products/retail/backend/api/retail_api.py`'s
   `create_sale()` always resolves `unit_price`/`tax_rate` from the
   product row), proven by
   `products/retail/tests/retail_financial_authority_test.py::test_server_ignores_manipulated_totals_and_unit_price`
   (279-suite, rerun this phase, still passing).
3. **The response the Android client reads is the server's own,
   verbatim.** `SaleContractTest.kt::saleResult_deserializes_the_full_authoritative_contract`
   constructs the exact worked-example JSON (`subtotal:100.0,
   discount_amount:20.0, tax_amount:8.0, total:88.0`) and proves `SaleResult`
   deserializes it correctly, and
   `manipulated_response_fields_are_read_verbatim_never_recomputed_client_side`
   proves there is no client-side recomputation that could re-derive a
   different number from an internally-inconsistent fixture.
4. **`RetailScreens.kt`'s checkout success path** (source-reviewed,
   quoted from the actual file after this phase's fix):
   `val authoritativeTotal = r.data?.total ?: previewTotal` followed by
   `successTotal = authoritativeTotal` — the value shown to the cashier
   and stored in `successTotal` is the server's `total`, never the local
   `previewTotal` (itself never taxed/discounted, used only to clamp an
   optional credit down-payment before submission).

## Android zero-tax defect (AUDIT-002) — result

**Fixed on both sides**, confirmed independently:

- **Server side** (Wave 0, unchanged this phase): `create_sale()` never
  trusts client tax/discount fields — proven by
  `retail_financial_authority_test.py::test_android_style_zero_tax_payload_still_computes_real_tax`.
- **Client side** (this phase, the actual remaining gap): the client no
  longer *displays* a client-computed, always-untaxed total as if it were
  authoritative. `SaleContractTest.kt::android_style_zero_tax_request_still_yields_a_taxed_authoritative_response`
  proves the response-reading path correctly surfaces `tax_amount: 15.0,
  total: 115.0` for a zero-tax-shaped request against a 15%-tax product.

## Clinic payment contract — result

Worked example (a $100 invoice), proven via
`PaymentContractTest.kt` against the exact Wave 0 response shapes:

| Request | Contract-test result |
|---|---|
| `amount = 0.00` | rejection response deserializes correctly, `data == null` |
| `amount = -10.00` | same |
| `amount = 40.00` | `total_paid: 40.0, outstanding_balance: 60.0, invoice_status: "partial"` |
| same `idempotency_key` replayed | same payment `id` returned, not a new one |
| `amount = 70.00` (after 40 paid) | rejection response references "outstanding balance" |
| `amount = 60.00` (after 40 paid) | `outstanding_balance: 0.0, invoice_status: "paid"` |

`CreatePaymentRequest` now always carries a real `idempotency_key`
(previously absent entirely — see `android-source-inventory.md`).

**Known limitation** (not fixed this phase, documented honestly): the
Retrofit client throws `HttpException` for the actual 400 rejection
responses rather than deserializing them into `CreatePaymentResponse`, so
`PaymentSheet`'s `r.status`-based branch is not the literal code path a
real rejection takes — see `clinic-device-test-report.md`'s "Honest bottom
line" and `android-residual-risk-register.md`. The payment is still never
incorrectly recorded as successful either way.

## What is NOT proven by this report

None of the above was observed against a real running embedded server on
a real device — every proof above is either (a) a Kotlin unit test
running the actual production data-model/serialization code on the JVM,
or (b) the unchanged Python backend's own 279-test suite, or (c) direct
source reading of the actual shipped code. No claim of end-to-end,
on-device financial correctness is made.
