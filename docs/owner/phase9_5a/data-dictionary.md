# Phase 9.5A — Data Dictionary

Full column-by-column definitions for each table already appear in their own design doc (see
`data-model.md`'s index). This dictionary is the canonical reference for **shared types and enums**
reused across multiple tables, so they stay consistent everywhere they appear.

## Shared enum vocabularies

| Enum | Values | Used by |
|---|---|---|
| Employment status | `PENDING, ACTIVE, SUSPENDED, TERMINATED, ARCHIVED` | `employee_profiles.employment_status` |
| Lead status | `NEW, NOT_INTERESTED_NOW, POTENTIAL, FOLLOW_UP, UNDER_OBSERVATION, QUALIFIED, CONFIRMED, LOST, ARCHIVED` | `leads.status` |
| Priority | `LOW, MEDIUM, HIGH` | `leads.priority`, `shared_management_notes.priority` |
| Location source | `GPS, NETWORK, MANUAL, IMPORTED` | `customer_locations.source` |
| Quote status | `DRAFT, SENT, ACCEPTED, REJECTED, EXPIRED, CANCELLED` | `quotes.status` |
| Sales order status | `DRAFT, CONFIRMED, CANCELLED, FULFILLED` | `sales_orders.status` |
| Invoice status | `DRAFT, ISSUED, PARTIALLY_PAID, PAID, VOID, REFUNDED, PARTIALLY_REFUNDED` | `commercial_invoices.status` |
| Refund status | `DRAFT, APPROVED, PAID, VOID` | `commercial_refunds.status` |
| Commission entry status | `PENDING, EARNED, APPROVED, PAID, REVERSED, CANCELLED, DISPUTED` | `commission_ledger_entries.status` |
| Commission rule type | `PERCENTAGE_OF_PAYMENT, FIXED_AMOUNT, PERCENTAGE_FIRST_SALE, PERCENTAGE_RENEWAL` (last two schema-reserved, not implemented) | `commission_rule_versions.rule_type` |
| Expense status | `DRAFT, SUBMITTED, APPROVED, REJECTED, PAID, VOID` | `expenses.status` |
| Management note status | `OPEN, IN_PROGRESS, DONE, ARCHIVED` | `shared_management_notes.status` |
| Management note visibility | `MANAGEMENT_ONLY, SPECIFIC_EMPLOYEES, ALL_STAFF` | `shared_management_notes.visibility` |
| Platform category | `WINDOWS, ANDROID, IOS, MOBILE` | `device_policy_platform_rules.platform_category` |
| Mobile session platform | `WEB, ANDROID, IOS` | `owner_staff_sessions.platform`, `employee_presence_sessions.platform` |
| Presence source | n/a — presence state is derived, never stored (Milestone 5) | — |

All stored as plain `String(N)` columns (never a Postgres native `ENUM` type) — matches the existing
codebase-wide convention confirmed in Milestone 1's audit (`Customer.lifecycle_status`,
`Subscription.status`, etc. are all plain strings); introducing a native enum type for only the new
tables would be an inconsistent pattern within the same schema.

## Shared column-naming conventions

- Every money amount: `<name>_amount` or `amount`/`total`/`subtotal`, type `Numeric(12,2)` (matches
  `PaymentRecord.amount`'s existing precision), always paired with a `currency` (String(3), ISO 4217)
  column.
- Every "who did this" reference: `<verb>_by_staff_user_id` (when the actor must be an authenticated
  account, e.g. approvals) or `<verb>_by_employee_profile_id` (when the actor is naturally scoped to
  an employee's own work, e.g. lead creation) — never both interchangeably on the same table; the
  choice is fixed per table and documented in its own design doc.
- Every time-bounded/historical row: `effective_from`/`effective_until` (open-ended ranges — pricing,
  commission rules, device-policy overrides) or `starts_at`/`expires_at` (matches the existing
  `DeviceSlotException` naming exactly, reused for the new device-policy overrides) — the choice
  matches whichever existing precedent the table is modeled after.
- Every foreign key to a UUID PK: `<referenced_table_singular>_id`.

## Full per-table field lists

See the design doc listed in `data-model.md`'s index table for the authoritative field-by-field
definition of each new table — not duplicated here to avoid the two documents drifting out of sync.
