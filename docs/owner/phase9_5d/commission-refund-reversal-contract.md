# Phase 9.5D — Commission Refund Reversal Contract

## Trigger

`refunds.py::confirm_refund()` calls `ledger.py::reverse_commissions_for_refund()` immediately after the refund is marked `PAID` and the invoice's own status is updated (`REFUNDED`/`PARTIALLY_REFUNDED`) — the same real transaction, not a deferred/async step.

## Proportionality, not blanket reversal

A refund reverses each active earning entry against the invoice by the same fraction the refund represents of the money actually collected:

```
proportion = min(refund_amount / collected_amount, 1)
reversal_amount(entry) = (entry.commission_amount - already_reversed(entry)) * proportion
```

This is deliberate: a 50% refund must claw back roughly 50% of the commission that was paid on that invoice, not 100% of it. `already_reversed(entry)` sums any prior reversal rows pointing at the same entry, so a second, later partial refund against an already-partially-reversed entry only reverses the *remaining* balance — it can never reverse more than the entry originally earned, and a reversal_amount that rounds to `0.00` is silently skipped (not an error — nothing left to reverse, or the refund is proportionally too small to move a cent at this entry's scale).

## Multiple earning entries per invoice

Because Milestone 11 allows multiple `PaymentAllocation` rows against one invoice (from one or several Payments), `reverse_commissions_for_refund()` iterates every active (non-fully-reversed, `EARNED`/`APPROVED`/`PAID`) entry for the invoice and reverses each independently by the same proportion — not just the most recent one.

## Append-only, always

Every reversal is a brand-new `CommissionLedgerEntry` row: `reversal_of_ledger_entry_id` set to the original entry's id, `commission_amount` negative, `status="REVERSED"`. The original entry's own `commission_amount` and `status` are never written to by any reversal path — `test_refund_creates_append_only_proportional_reversal` asserts this directly by re-reading the original row after the reversal and checking both fields are byte-for-byte unchanged.

## What a reversal does NOT do

- It does not change the original entry's `status` away from `EARNED`/`APPROVED`/`PAID` — a reversal coexists with the entry it reverses, it does not supersede it in the row's own lifecycle.
- It does not, by itself, re-open a `PAID` payout — reversing a commission that has already been physically paid out is a real-world clawback event outside this system's scope (no bank-reversal integration exists); the ledger simply records the negative entry as the permanent record that a clawback is owed.
- It has no interaction with `PaymentAllocation.reverse_allocation()` (Milestone 11) — allocation reversal and refund-driven commission reversal are two independent, separately-triggered operations that happen to share the same proportional-math shape.
