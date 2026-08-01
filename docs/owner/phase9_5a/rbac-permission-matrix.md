# Phase 9.5A Milestone 17 — RBAC Permission Matrix (real code)

## Real work: `owner/app/staff/seed_data.py` extended additively

56 new permission codes added (125 total, up from 69), 8 new categories (`EMPLOYEES`,
`SALES_PIPELINE`, `COMMERCIAL_SALES`, `COMMISSIONS`, `EXPENSES`, `LICENSING_OPERATIONS`, `REPORTS`,
`MANAGEMENT_COLLABORATION`), zero existing permission codes renamed or removed, zero duplicate codes
(verified: `len(codes) - len(set(codes)) == 0`). No new roles created — Bahaa and Awab remain two
separate `SUPER_ADMIN` accounts (RBAC is already account-independent; nothing new needed here).

## No redundant roles

Confirmed: the 5 existing roles (SUPER_ADMIN/SALES/SUPPORT/FINANCE/VIEWER) cover every real need in
this phase's scope — extended with new permission grants, never replaced.

## Grant summary (full detail in `owner/app/staff/seed_data.py`, reproduced here for review)

| Role | New grants (own-scoped/creation permissions) | Deliberately withheld |
|---|---|---|
| SALES | `employees.view_own`, `leads.create/view_own/update_own/convert`, `customers.view_own/update_own/capture_location`, `quotes.create`, `orders.create`, `invoices.create`, `commissions.view_own`, `expenses.create/view_own`, `device_policy.view`, `dashboard.view_own`, `reports.view_own` | `leads.view_all`, any `commissions.*` beyond own, `invoices.issue`, `pricing.override`, `refunds.*`, `device_policy.manage` |
| SUPPORT | `customers.view_own`, `leads.view_own`, `device_policy.view` | anything financial/commission/pricing |
| FINANCE | `payments.confirm`, `invoices.issue`, `refunds.create/approve`, `commissions.view_all/approve/pay/reverse`, `expenses.view_all/approve/pay/void`, `customers.view_all`, `dashboard.view_all`, `reports.view_all` | `leads.*` (not a pipeline role), `device_policy.manage`, `pricing.override` |
| VIEWER | `leads.view_all`, `customers.view_all`, `device_policy.view`, `dashboard.view_all`, `reports.view_all` | `commissions.*` (personal earnings), `expenses.*` (financial-sensitive), `employees.*` (HR data) |
| SUPER_ADMIN | everything, via the existing wildcard | n/a |

`pricing.override`, `device_policy.manage`, `employees.terminate`, `employees.assign_role`,
`commissions.calculate` (manual re-run), `management_notes.manage` are **not granted to any role below
SUPER_ADMIN** — matching the exact existing precedent set by `activation_policy.manage`/
`signing_keys.manage` (Phase 8V-P2) for "changing this changes the security/commercial posture of
everything downstream."

## Interaction with the existing, unscoped `customers.view`/`leads`-adjacent permissions

The pre-existing `customers.view` permission (Phase 5) continues to gate the current, unchanged
`/customers` UI routes exactly as before — Phase 9.5A does not touch those routes. The new
`customers.view_own`/`customers.view_all` permissions are scoped specifically to the new
ownership-aware `/api/operations/v1/customers` surface (Milestone 19, not yet routed this phase). A
role can legitimately hold both (e.g. FINANCE now has old `customers.view` *and* new
`customers.view_all`) without conflict — they gate two different, non-overlapping code surfaces.

## Real verification

`owner/tests/` RBAC/permission/preflight/staff-scoped tests: **35/35 passing** against the extended
seed data (`all_permissions_seeded`/`role_permissions_synced` preflight checks — Milestone 3, Phase
8V-P9 — still pass, confirming no drift between `PERMISSIONS` and `ROLES`).
