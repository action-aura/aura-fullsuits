# Phase 9.5D — Milestone 11: Payment Allocation Contract

`app/commercial_sales/allocation.py`, on `PaymentAllocation` (migration `b7e4a2c91f30`, added ahead of this milestone during M6's work) — a genuine, acknowledged extension beyond Phase 9.5A's original implicit 1-payment-to-1-invoice design (`PaymentRecord.commercial_invoice_id`), not a contradiction of it (see `commercial-funnel-contract.md`'s Milestone 11 note).

## Functions

- `unallocated_payment_balance(payment) -> Decimal` — `payment.amount` minus the sum of its non-reversed allocations.
- `allocate_payment(*, payment, invoice, amount, actor_staff_user_id) -> PaymentAllocation` — requires `payment.status == "CONFIRMED"`; rejects currency mismatch; takes a row-level lock on the invoice (`SELECT ... FOR UPDATE`) for the duration, so two concurrent allocation attempts against the same invoice serialize rather than both reading a stale outstanding balance; validates via `calculator.validate_allocation_amount()` (Milestone 3, unchanged); recomputes and persists the invoice's new status via `calculator.resolve_invoice_status()` (Milestone 3) fed by `invoices.py::confirmed_allocated_amount()` (Milestone 9) — the invoice's `status` column is the only place this is stored, never a redundant `amount_paid` field.
- `reverse_allocation(allocation, *, reason, actor_staff_user_id) -> PaymentAllocation` — reason required; also takes the invoice row lock; recomputes invoice status downward (reversal can only reduce the allocated sum, so status can only move `PAID`→`PARTIALLY_PAID`→`ISSUED`, never re-trigger a `VOID`/`DRAFT` override — `resolve_invoice_status()` already guards those as never overridden by a payment-sum computation, unchanged from Milestone 3/9).

## Concurrency

The `SELECT ... FOR UPDATE` on the invoice row is the real mechanism (not just documented intent) — matching the same real Postgres row-locking pattern already proven in this codebase (Phase 6's `test_simultaneous_final_slot_activations_only_one_accepted`, this phase's own `numbering.py`). A dedicated multi-threaded concurrency test for allocation specifically is deferred to Milestone 24 (financial property/concurrency test pass), matching the phase's own milestone ordering — the locking mechanism itself is identical in shape to the already-concurrency-proven `numbering.py`.

## Partial payment and multiple payments per invoice

Both directions are proven by test: `test_partial_allocation_marks_invoice_partially_paid` (one payment, less than the invoice total) and `test_multiple_partial_payments_sum_to_paid` (two separate confirmed Payments, each partially allocated, summing to `PAID`) — "one Invoice may receive multiple confirmed Payments" (spec text) is real, not aspirational.

## Reversal frees the payment balance for reallocation

`test_reversed_allocation_frees_payment_balance_for_reallocation` — after reversing an allocation, `unallocated_payment_balance()` returns the freed amount, and that same Payment can be validly allocated to a *different* Invoice. Reversal never deletes the original `PaymentAllocation` row (it sets `reversed_at`/`reversed_by_staff_user_id`/`reversal_reason` — Non-Negotiable Rule 16: no hard delete of commercial history).

## Test coverage

`tests/test_phase9_5d_allocation.py` — 10 tests: full allocation → `PAID`, partial → `PARTIALLY_PAID`, unconfirmed-payment rejection, allocation-exceeds-payment rejection, allocation-exceeds-outstanding rejection, cross-currency rejection, multiple payments summing to paid, reversal-requires-reason, reversal reduces invoice status back down, and reversed-allocation-frees-balance-for-reallocation.
