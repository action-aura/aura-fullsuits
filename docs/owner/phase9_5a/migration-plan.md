# Phase 9.5A Milestone 20 — Migration Plan

## Starting point

Current real migration head: `0f8d55b753ed` (confirmed, Milestone 1's audit). This phase's migration(s)
chain from there — no existing migration is edited or reordered.

## Single migration vs. several — decision: one migration per bounded context, applied in dependency order

Rather than one giant migration (hard to review, hard to roll back partially) or one-migration-per-table
(excessive ceremony for tightly-coupled table groups), this phase uses **one migration per bounded
context**, matching `bounded-context-map.md`'s own module boundaries — each migration is independently
reviewable and, if something is wrong with e.g. the Commissions tables specifically, only that one
migration needs correcting, not the whole batch.

## Real ordering (respects foreign-key dependencies — each migration's tables only reference tables
that already exist by that point in the chain)

```
0f8d55b753ed (current head)
  -> 9.5a_001_employee_profiles          (employee_profiles, employee_presence_sessions)
  -> 9.5a_002_device_policy               (device_policy_profiles, device_policy_platform_rules,
                                            subscription_device_policy_overrides)
  -> 9.5a_003_leads_and_locations         (leads + all lead child tables, customer_locations,
                                            customer_interactions, customer_followups,
                                            Customer.converted_from_lead_id additive column)
  -> 9.5a_004_commercial_sales            (quotes..commercial_refunds, idempotency-keys table,
                                            PaymentRecord.commercial_invoice_id additive column,
                                            Subscription.sales_order_id additive column)
  -> 9.5a_005_commissions                 (commission_plans .. commission_payout_lines --
                                            depends on employee_profiles [004] and commercial_sales [004])
  -> 9.5a_006_expenses                    (expense_categories, expenses -- depends only on
                                            employee_profiles [001])
  -> 9.5a_007_management_notes            (shared_management_notes + children -- depends only on
                                            owner_staff_users, already exists)
  -> 9.5a_008_daily_reports               (daily_activity_snapshots -- no FK dependencies at all)
  -> 9.5a_009_mobile_sessions             (StaffSession additive columns only -- refresh_token_hash,
                                            refresh_token_family_id, access_token_last_issued_at, platform)
```

(Exact revision hash IDs assigned by Alembic at generation time in Milestone 21 — the sequence and
dependency ordering above is the real, reviewed plan; the placeholder names above map 1:1 to what
Milestone 21 actually generates.)

## Rollback strategy

Every migration in this set is purely additive (`CREATE TABLE`/`ALTER TABLE ... ADD COLUMN`, all new
columns nullable or defaulted) — every one has a safe, real `downgrade()` (`DROP TABLE`/
`ALTER TABLE ... DROP COLUMN`) with **zero data-loss risk on a fresh/synthetic database**, since no
migration in this set ever touches a pre-existing row's data. On a database with real Phase 9.5A data
already in the new tables, downgrading would of course discard that data — the same honest caveat
`docs/owner/phase9/migration-runbook.md` already states for any migration in this codebase; not a new
risk class.

## Testing requirement (Milestone 21)

Each migration applied and rolled back on: (a) a fresh/empty database, (b) the same populated synthetic
Phase 9 staging database used for Phase 9's own real restore-drill evidence (reused, not recreated) —
proving the migration set is safe against a database that already has real Phase 5-9 schema and data in
it, not just a pristine one.

## Schema-drift check

After applying the full chain, `alembic check`-equivalent (comparing live DB schema against the
SQLAlchemy model metadata) confirms zero drift — every new model's `Mapped[...]` column matches its
migration's real DDL exactly.
