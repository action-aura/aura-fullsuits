# Phase 9.5B Milestone 20 — Migration Impact Report

## Decision: one additive column, nothing else

The complete Phase 9.5A schema (`EmployeeProfile`, `EmployeePresenceSession`, `StaffUser`, `StaffSession`,
`StaffInvitation`, RBAC tables) already supported every real operation this phase built. The **only**
schema change: `owner_staff_invitations.employee_profile_draft` (nullable `Text`, migration
`af7831a6dc4d`, chains from Phase 9.5A's head `3c0d51d82d8c`) — see
`employee-onboarding-security-contract.md` for why (a JSON-encoded employee-profile draft carried on the
existing invitation, materialized transactionally on acceptance).

No new table. No new index (confirmed sufficient at real Owner scale by Milestone 19's own measurement —
`employee_number`'s existing `UNIQUE` index and `staff_user_id`'s existing `UNIQUE` FK index cover every
real query this phase's `list_employees()`/`find_own_profile()` perform). No column renamed/removed/
retyped anywhere in the existing schema.

## Real testing performed (same discipline as Phase 9.5A's own migration)

- **Empty database**: `aura_owner_test`, migrated fresh via `alembic upgrade head` (exercised by the full
  Phase 9.5B test suite's own `_migrated_schema` fixture).
- **Populated database**: applied to the real `aura_owner_dev` and `aura_owner_staging` databases
  (the latter still carrying Phase 9's own restore-drill data) — clean, no errors, nullable column needs
  no `server_default` (unlike Phase 9.5A's `platform` column, which was `NOT NULL`).
- **Round-trip**: `upgrade → downgrade → upgrade` against `aura_owner_dev`, clean both directions (a
  nullable additive column's `downgrade()` is a plain `DROP COLUMN`, no named-constraint complication like
  Phase 9.5A's own `sales_order_id`/`converted_from_lead_id` additions needed).
- **Schema-drift check**: implicit in the full 546-test regression (Milestone 22/final) — a live/model
  mismatch would fail ORM-level tests immediately.

## No data loss, no licensing/customer-data impact

The new column touches only `owner_staff_invitations`, a table with zero relationship to
`License`/`Installation`/`Customer`/`Subscription` — structurally impossible for this migration to affect
licensing or commercial data, confirmed by inspecting the model's own foreign keys (none reference those
tables).
