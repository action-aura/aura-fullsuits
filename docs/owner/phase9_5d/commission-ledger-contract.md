# Phase 9.5D — Milestone 15: Commission Ledger Contract

`app/commissions/ledger.py` — closes Milestone 1's audit gap: `CommissionLedgerEntry`/`CommissionPayoutBatch`/`CommissionPayoutLine` were MODEL PRESENT, SERVICE MISSING. Lives in the existing `app/commissions/` package alongside `services.py` (Phase 9.5A, unchanged) and `management.py` (Milestone 14).

## The real gap this milestone found and closed

`CommissionLedgerEntry`'s original Phase 9.5A uniqueness constraint deduped on `source_payment_record_id` — written before `PaymentAllocation` existed. Milestone 11 already proved a single confirmed Payment can be split-allocated across multiple Invoices (`test_multiple_partial_payments_sum_to_paid`, `test_reversed_allocation_frees_payment_balance_for_reallocation`). Combined with the Non-Negotiable rule "partial allocations create proportional earnings," a per-payment constraint would silently block the second allocation's legitimate earning the first time a real customer split one payment across two invoices. Fixed via migration `a3c8e5d29f47`: added `source_payment_allocation_id`, moved the partial unique index (`WHERE reversal_of_ledger_entry_id IS NULL`) onto it. The table was empty (MODEL PRESENT SERVICE MISSING) — no backfill needed.

## Functions

- `post_earning_for_allocation(allocation, actor_staff_user_id) -> CommissionLedgerEntry | None` — the real earning trigger, wired into `allocation.py::allocate_payment()`. Returns `None` (not an error) when the crediting employee (the invoice's `created_by_employee_profile_id`) has no active commission plan/rule assignment for the allocation's date — nothing to earn, not a failure.
- `approve_commission_entry(entry, actor_staff_user_id) -> CommissionLedgerEntry` — `EARNED` → `APPROVED`. Beneficiary self-approval blocked unconditionally.
- `reverse_commission_entry(entry, *, reversal_amount, reason, actor_staff_user_id) -> CommissionLedgerEntry` — the single append-only reversal primitive. Creates a NEW row (`reversal_of_ledger_entry_id` set, negative `commission_amount`); never edits the original.
- `reverse_commissions_for_refund(invoice, *, refund_amount, collected_amount, reason, actor_staff_user_id) -> list[CommissionLedgerEntry]` — the refund-driven reversal trigger, wired into `refunds.py::confirm_refund()`. Reverses every active earning entry against the invoice proportionally to `refund_amount / collected_amount`, capped at 1.0 and net of any already-reversed amount on that entry (so a second partial refund against an already-partially-reversed entry reverses only the remaining balance, never double-reverses).
- `create_payout_batch(fields, actor_staff_user_id) -> CommissionPayoutBatch`, `approve_payout_batch(batch, actor_staff_user_id) -> CommissionPayoutBatch` (creator self-approval blocked), `record_payout(entry, batch, actor_staff_user_id) -> CommissionPayoutLine` (`APPROVED` entry + `APPROVED` batch required).

## Duplicate-earning and duplicate-payout: DB-level, not application-level

Both "duplicate X must be impossible under concurrency" rules are enforced by real Postgres unique constraints, not merely an app-layer pre-check (a pre-check alone cannot rule out two concurrent transactions both passing the check before either commits):

- Earning: partial unique index `uq_commission_ledger_one_entry_per_allocation` on `source_payment_allocation_id` (this milestone).
- Payout: `uq_commission_payout_one_per_entry` on `commission_ledger_entry_id` (Phase 9.5A, already real — confirmed unchanged, no fix needed for this specific rule).

Both `IntegrityError`s are caught and re-raised as stable `CommissionError` codes (`COMMISSION_ALREADY_EARNED_FOR_ALLOCATION`, `COMMISSION_ALREADY_IN_PAYOUT_BATCH`).

## `version` column — deliberately not added

Every other mutable commercial-sales document this phase (`Quote`, `SalesOrder`, `CommercialInvoice`, `CommercialRefund`, `PaymentAllocation`, `QuoteLine`) carries an optimistic-lock `version` column. `CommissionLedgerEntry`/`CommissionPayoutBatch`/`CommissionPayoutLine` deliberately do not: the specific correctness risk those `version` columns close is a lost-update race on a *mutable* row with multiple legal states reachable by different actors. Here, the two concurrency-sensitive operations (duplicate earning, duplicate payout) are instead closed by a strictly stronger mechanism — a real DB-level unique constraint that makes the bad state structurally uncreatable, not just detectable after the fact. A `version` counter would be redundant insurance on top of a constraint that already can't be raced.

## Test coverage

`tests/test_phase9_5d_commission_ledger.py` — 13 tests, one per Commission Non-Negotiable Rule (see `commission-attribution-policy.md` for the full rule-to-test map), including a real 8-thread concurrency race proving duplicate earning is structurally impossible, not just usually-avoided.
