# Phase 5 -- Owner Subscription Domain (Part L/M)

## Scope
Owner-side records of what a customer organization has commercially agreed to -- never a live connection into the product they're using. `owner_subscriptions` links `owner_customers` -> `owner_plans` (which link to `owner_products`), with `owner_subscription_items`/`owner_subscription_addons` for line-item detail, `owner_subscription_status_history` for the full transition trail, and `owner_renewal_records`/`owner_payment_records` for manual commercial bookkeeping.

## Renewal records preserve full history (Part M)
Every renewal (`record_renewal()`) stores `previous_end_date`, `new_end_date`, `previous_plan_id`, `new_plan_id`, `reason`, and `approved_by_staff_user_id` -- nothing is overwritten in place; the subscription's `end_date`/`plan_id` are updated, but the renewal record itself is a permanent, queryable trail.

## Payment records are NOT accounting revenue (Part M, explicit spec instruction)
`owner_payment_records` stores manually-entered `amount`/`currency`/`method`/`status`/`reference`, with `recorded_by_staff_user_id` and `verified_by_staff_user_id` as separate fields (supporting a maker-checker-style review, though not enforced as mandatory this phase). The dashboard and every UI surface referring to these records is labeled "commercial records," never "revenue" or "accounting" -- this is a deliberate distinction the spec requires and this implementation honors: no code path aggregates these into a claimed audited-revenue figure. No payment gateway is integrated; no card/bank credential is ever stored (`payments.correct` exists specifically so a mis-recorded manual entry can be corrected without deleting the audit trail).

## Test evidence
`owner/tests/test_subscriptions.py` (5/5): valid/invalid transitions, status-history recording with reason, terminal-state cancellation rejection, payment-status enum validation. Live end-to-end HTTP smoke test performed this phase: customer -> plan -> subscription -> payment record, full chain exercised through real HTTP requests against a real Postgres-backed app instance (see `owner-test-report.md`).
