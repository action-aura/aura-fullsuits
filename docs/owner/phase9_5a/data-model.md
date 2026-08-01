# Phase 9.5A Milestone 20 — Consolidated Data Model

Full field-level detail lives in each capability's own design doc (linked below); this document is the
one-page index plus the cross-cutting conventions every new table follows.

## New tables by module (`owner/app/models/*.py`)

| Module file | Tables | Design doc |
|---|---|---|
| `employees.py` | `employee_profiles`, `employee_presence_sessions` | `employee-domain-model.md`, `employee-presence-contract.md` |
| `leads.py` | `leads`, `lead_status_history`, `lead_assignments`, `lead_interactions`, `lead_followups`, `lead_notes`, `lead_product_interests`, `customer_locations`, `customer_interactions`, `customer_followups` | `lead-customer-domain-model.md`, `customer-location-contract.md` |
| `commercial_sales.py` | `quotes`, `quote_lines`, `sales_orders`, `sales_order_lines`, `commercial_invoices`, `commercial_invoice_lines`, `commercial_refunds`, `commercial_operations_idempotency_keys` | `commercial-document-lifecycle.md`, `payment-and-fulfillment-contract.md` |
| `commissions.py` | `commission_plans`, `commission_rule_versions`, `employee_commission_plan_assignments`, `commission_ledger_entries`, `commission_payout_batches`, `commission_payout_lines` | `commission-domain-design.md` |
| `expenses.py` | `expense_categories`, `expenses` | `mini-financial-ledger-design.md` |
| `management_notes.py` | `shared_management_notes`, `management_note_visibility_grants`, `management_note_comments` | `management-notes-design.md` |
| `daily_reports.py` | `daily_activity_snapshots` | `daily-reporting-contract.md` |
| `activation_governance.py` (extended) | `device_policy_profiles`, `device_policy_platform_rules`, `subscription_device_policy_overrides` | `multi-device-policy-design.md` |

## Additive columns on existing tables (zero existing columns renamed/removed/retyped)

| Table | New column | Reason |
|---|---|---|
| `owner_customers` | `converted_from_lead_id` (FK, nullable) | Lead-conversion traceability |
| `owner_payment_records` | `commercial_invoice_id` (FK, nullable) | Reuse `PaymentRecord`, no parallel payment table |
| `owner_subscriptions` | `sales_order_id` (FK, nullable) | Fulfillment bookkeeping link |
| `owner_staff_sessions` | `refresh_token_hash`, `refresh_token_family_id`, `access_token_last_issued_at`, `platform` (all nullable/defaulted) | Mobile session support, additive |

## Cross-cutting conventions (every new table)

- `UUIDPKMixin`/`TimestampMixin` — reused exactly as every existing model already uses them (Milestone
  1's audit confirmed this is the universal existing pattern; not a new one).
- Public UUIDs only, never an exposed integer PK.
- Money: `Numeric`, currency always an explicit sibling column.
- Historical/append-only pattern where correctness demands it (price versions, commission ledger
  entries, expense corrections, lead/customer assignment history) — a new row, never an in-place edit
  of a posted/historical fact.
- Optimistic `version` column on every table with concurrent-edit risk (documents, notes, profiles).
- `created_by`/`updated_by` staff or employee-profile references where attribution matters.
- Soft archive (`archived_at`) where history must be retained, never a hard delete once real business
  activity exists.
- Foreign keys default to `ON DELETE RESTRICT` (SQLAlchemy/Postgres default when no `ondelete` is
  specified) — no cascading deletes introduced this phase; a referenced employee/customer/lead cannot
  be hard-deleted while dependent rows exist, by construction.

## Full detail

See `data-dictionary.md` (column-by-column), `entity-relationship-design.md` (relationships/cardinality),
`migration-plan.md` (ordering and rollback strategy).
