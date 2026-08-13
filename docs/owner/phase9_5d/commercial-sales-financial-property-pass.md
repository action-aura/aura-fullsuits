# Phase 9.5D — Milestone 24: Financial Property/Rounding/Idempotency/Concurrency Pass

Per the execution plan: "every financial calculation gets property-based/table-driven tests... in addition to the standard unit/integration tests." Most of this discipline was already applied incrementally per-milestone, not deferred here — this is the consolidated verification pass, plus one real gap it found.

## Already proven per-milestone (re-verified, not re-derived)

- **Rounding**: `app/commercial_sales/calculator.py` — `ROUND_HALF_UP` at 2 decimal places, 16 test functions (40+ parametrized cases) in `test_phase9_5d_financial_calculator.py` covering line/document totals, proportional discount allocation with deterministic remainder handling, negative/NaN/infinity rejection, and a dedicated `test_price_snapshot_immutability_property()`. Commission rounding reuses the identical convention (Milestone 14's own `commission-basis-and-rounding.md`).
- **Idempotency**: `CommercialOperationsIdempotencyKey` (shared ledger, `(idempotency_key, operation_code)` unique) backs `create_order_from_quote()`, `create_invoice_from_order()`, `fulfill_order()` — each independently tested for identical-payload replay (returns the original result) and conflicting-payload replay (rejected, `IDEMPOTENCY_CONFLICT`) at their own milestones (M8/M9/M13). `confirm_payment()`/`post_earning_for_allocation()` use idempotency-by-rejection instead (a second attempt on an already-terminal state is rejected outright, not replayed) — a deliberate, documented design choice (`payment-maker-checker-policy.md`), not an oversight.
- **Concurrency**: real thread-race tests already exist for document numbering (`numbering.py`, 12 threads, M5), payment allocation (`allocate_payment()`'s row lock, M11), fulfillment (`fulfill_order()`'s row lock + incompatible-state guard, M13's 8-item closure), and commission earning (`post_earning_for_allocation()`'s DB-level partial unique index, M15, 8 threads).

## Real gap found and closed this milestone: refund over-refund race

`confirm_refund()` never re-validated the refund total against what was actually collected, nor locked the invoice — the only document-confirmation path in the whole phase missing this pattern. `create_refund()`'s own `validate_refund_amount()` check only looks at *currently-PAID* refunds (`total_confirmed_refunds()` filters `status == "PAID"`), so two `DRAFT` refunds can each independently pass validation (each individually within the refundable balance) while their combined total exceeds it — and without a lock at confirm time, both could even be confirmed concurrently, over-refunding the customer past what was actually collected.

Fixed by locking the invoice (`SELECT ... FOR UPDATE`, matching `allocate_payment()`'s exact pattern) and re-validating `already_refunded + refund.amount <= collected` immediately before the `DRAFT`/`APPROVED` → `PAID` transition, raising the existing `REFUND_EXCEEDS_REFUNDABLE` code (reused, not a new one) if it would be exceeded.

Proven two ways in `tests/test_phase9_5d_refunds.py`:
- **Sequential**: two 150-against-a-200-collected-invoice refunds — the first confirms clean, the second is rejected at confirm time (not merely at create time) with `REFUND_EXCEEDS_REFUNDABLE`, and its own status stays `APPROVED` (unchanged, not silently advanced).
- **Real 4-thread concurrency race**: four 100-each refunds approved against a 200-collected invoice (400 total requested, only 200 available — at most 2 can legitimately succeed), confirmed concurrently from separate threads; asserts the actual `PAID` total across all four never exceeds 200 regardless of thread scheduling order.

## Verification

Full Owner regression: 873/873 (up from 865 at Milestone 22 — 8 new Milestone 23 IDOR tests included). `tests/test_phase9_5d_refunds.py`: 10/10 including the two new tests.
