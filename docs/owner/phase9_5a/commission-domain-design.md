# Phase 9.5A Milestone 12 — Commission Domain Design

## New models (`owner/app/models/commissions.py`)

```
commission_plans: id, plan_code (unique), name, description, is_active, created_at

commission_rule_versions: id, commission_plan_id (FK), rule_type (PERCENTAGE_OF_PAYMENT |
  FIXED_AMOUNT | PERCENTAGE_FIRST_SALE | PERCENTAGE_RENEWAL), rate_percentage (Numeric, nullable),
  fixed_amount (Numeric, nullable), currency (nullable, required if fixed_amount set),
  product_id (FK, nullable -- NULL = applies to all products), effective_from, effective_until
  (nullable), created_by_staff_user_id, created_at
  -- append-only: a rule change is a NEW row with a new effective_from + the old row's
  -- effective_until closed, exactly like PlanPrice's own historical-row convention

employee_commission_plan_assignments: id, employee_profile_id (FK), commission_plan_id (FK),
  effective_from, effective_until (nullable), created_by_staff_user_id, created_at

commission_ledger_entries: id, employee_profile_id (FK), commission_rule_version_id (FK -- pins the
  EXACT rule version used, so a later rule change never retroactively alters an already-earned entry),
  source_commercial_invoice_id (FK), source_payment_record_id (FK),
  base_amount (Numeric -- the payment amount the rule was applied to), rate_or_fixed_applied (Numeric),
  commission_amount (Numeric), currency,
  status (PENDING|EARNED|APPROVED|PAID|REVERSED|CANCELLED|DISPUTED, default PENDING),
  earned_at, approved_at, approved_by_staff_user_id, paid_at, reversal_of_ledger_entry_id (FK,
  nullable -- set only on a REVERSAL row, pointing back at the entry it reverses), dispute_reason
  (nullable), created_at
  -- APPEND-ONLY. A reversal is a NEW row with reversal_of_ledger_entry_id set and a negative
  -- commission_amount, never an UPDATE of the original entry's amount or status field beyond the
  -- state-machine-controlled status transitions listed above.

commission_payout_batches: id, batch_reference (unique), period_start, period_end, status
  (DRAFT|APPROVED|PAID), created_by_staff_user_id, approved_by_staff_user_id (nullable), created_at

commission_payout_lines: id, commission_payout_batch_id (FK), employee_profile_id (FK),
  commission_ledger_entry_id (FK, unique -- one entry can only ever be paid out once), amount, currency
```

## Canonical lifecycle (exactly as specified)

```
Commercial invoice created -> no commission entry yet
Payment confirmed -> eligibility evaluated (CommissionEligibilityService, Milestone 22)
  -> if eligible: commission_ledger_entries row created, status PENDING or EARNED
     (PENDING if the rule requires a waiting/clawback window -- not built this phase, so this
      milestone's real implementation always goes straight to EARNED; PENDING is reserved schema
      for a later phase's clawback-window rule type)
Management approval -> APPROVED (commissions.approve permission)
Payout -> PAID (via a commission_payout_batch)
Refund/cancellation of the source invoice -> a new REVERSAL ledger entry (negative amount,
  reversal_of_ledger_entry_id set), never an edit of the original EARNED/APPROVED/PAID entry
```

## Real invariants (all enforced by this schema + Milestone 22's service, not by convention alone)

- Employee sees own commission only (`commissions.view_own`, ownership filter on
  `employee_profile_id` — same query-filter pattern as Milestone 7).
- Management sees all (`commissions.view_all`).
- Finance/admin approves payout — `commissions.approve` not granted to SALES.
- Employee cannot approve own commission — service-level check: `approved_by_staff_user_id`'s
  underlying employee profile must differ from the ledger entry's own `employee_profile_id`, enforced
  in code, not just by permission (a Finance employee approving their *own* earned commission would
  still pass a naive permission check without this explicit rule).
- Employee cannot change commission plan — `employees.manage_commission_plan` (management only).
- Historical rules stay tied to historical entries — `commission_rule_versions` is append-only,
  `commission_ledger_entries.commission_rule_version_id` is a permanent pointer to the exact version.
- Refund doesn't silently delete an earned entry — reversal is always a new row.
- Duplicate payment event doesn't duplicate commission — `source_payment_record_id` has a real
  uniqueness constraint scoped to non-reversal ledger entries (a `PARTIAL UNIQUE INDEX` in Postgres:
  `UNIQUE (source_payment_record_id) WHERE reversal_of_ledger_entry_id IS NULL`), so re-processing the
  same confirmed-payment event twice cannot create two `EARNED` entries for it.
- Payout doesn't alter the original commercial payment — `commission_payout_lines` only ever reads
  `commission_ledger_entries`, never writes back to `PaymentRecord`/`CommercialInvoice`.
- `Decimal` used throughout — every amount column is `Numeric`, matching the codebase's existing
  convention everywhere else money is handled.
- Audited — `COMMISSION_EARNED`/`COMMISSION_APPROVED`/`COMMISSION_PAID`/`COMMISSION_REVERSED`
  (Milestone 23).
