# Phase 9.5D — Commission Attribution Policy

## Who earns the commission

The crediting employee for a `CommissionLedgerEntry` is the invoice's `created_by_employee_profile_id` — the salesperson who created the `CommercialInvoice` (which itself descends from the `SalesOrder` they created from the `Quote` they created). This is resolved fresh, once, at the moment `post_earning_for_allocation()` runs — never recomputed later.

## Historical credit is permanent (Non-Negotiable Rule 10)

A `CommissionLedgerEntry`'s `employee_profile_id` is written once, at earning time, and never updated. There is no code path anywhere in `app/commissions/` or `app/commercial_sales/` that re-derives or rewrites an existing ledger entry's `employee_profile_id` from current invoice/customer/lead ownership. Reassigning the underlying deal (e.g. changing `CommercialInvoice.created_by_employee_profile_id`, or reassigning the Customer/Lead to a different employee via `app/leads/services.py::assign_lead()`) after a commission has already been earned has zero effect on that historical row — proven directly in `tests/test_phase9_5d_commission_ledger.py::test_customer_reassignment_does_not_transfer_historical_commission_credit`.

This is a structural guarantee, not a runtime check: nothing in the codebase ever issues an `UPDATE` against `owner_commission_ledger_entries.employee_profile_id`.

## Rule-to-test map

The 13 Commission Non-Negotiable Rules, each with its proving test in `tests/test_phase9_5d_commission_ledger.py`:

1. No commission from Quote — `test_no_commission_from_quote_order_or_invoice_alone`
2. No commission from Order — `test_no_commission_from_quote_order_or_invoice_alone`
3. No commission from Invoice — `test_no_commission_from_quote_order_or_invoice_alone`
4. No commission from unconfirmed Payment — `test_no_commission_from_unconfirmed_payment`
5. Commission basis is confirmed Payment Allocation — `test_confirmed_allocation_is_the_real_earning_trigger`
6. Tax excluded by default — `test_tax_is_excluded_from_commission_base_by_default`
7. Partial allocations create proportional earnings — `test_partial_allocations_create_proportional_earnings`
8. Refunds create append-only proportional reversal entries — `test_refund_creates_append_only_proportional_reversal`
9. Historical earnings never overwritten/deleted — `test_refund_creates_append_only_proportional_reversal` (asserts the original row's own fields are unchanged after reversal)
10. Customer reassignment does not transfer historical commission credit — `test_customer_reassignment_does_not_transfer_historical_commission_credit`
11. Beneficiary cannot approve own adjustment — `test_beneficiary_cannot_approve_own_commission_entry`
12. Payout recording requires authority and an external reference — `test_payout_requires_reference_and_authority_and_approved_entry`, `test_payout_batch_creator_cannot_self_approve_batch`
13. Duplicate earning and duplicate payout impossible under concurrency — `test_duplicate_earning_for_same_allocation_rejected`, `test_duplicate_earning_impossible_under_real_concurrency` (real 8-thread race), `test_duplicate_payout_for_same_entry_rejected`

See `commission-ledger-contract.md` for the service contract and `commission-refund-reversal-contract.md` for the reversal-proportionality mechanics.
