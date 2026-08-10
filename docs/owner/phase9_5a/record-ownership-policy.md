# Phase 9.5A Milestone 7 — Record Ownership and Isolation Policy

## Ownership rule (applies to Lead and Customer alike)

An employee may access a Lead/Customer only when at least one holds:

1. They created it (`created_by_employee_profile_id`) **and** policy retains creator access — real
   policy decision: creator access is retained even after reassignment (an employee should still see
   what they originated), but creator access is *read-only* once reassigned away from them (they can no
   longer update/reassign/convert it — only the currently-assigned employee or management can).
2. They are the current assignee (`lead_assignments`/`Customer.assigned_sales_staff_id` — the open row
   with `unassigned_at IS NULL`, or the existing `Customer` column directly).
3. Team-based access: deferred — no team/group model exists yet (confirmed absent in Milestone 1's
   audit) and is not built this phase; `SPECIFIC_EMPLOYEES`-style sharing exists only for Management
   Notes (Milestone 14), not for Leads/Customers this phase.
4. Management explicitly shared it: deferred to the same later phase as #3 — no sharing-grant table is
   built this phase (would be new schema beyond "foundation"); management's own global-access role
   already covers the real near-term need.
5. Their role grants global access (`leads.view_all`/`customers.view_all` — SUPER_ADMIN, and
   optionally SUPPORT for read access per the existing RBAC precedent of SUPPORT already having
   `customers.view`).

## Enforcement point: the query layer, not just the permission check

Every list/detail/search/update query for Lead or Customer (and their child records — notes,
interactions, follow-ups, locations) must apply an ownership `WHERE` clause **in addition to** the
permission check, for any role that only has the `_own` variant of a permission
(`leads.view_own`/`customers.view_own`). A role with the `_all` variant skips the ownership filter
entirely (management). This is implemented as one shared query-building helper
(`apply_ownership_filter(stmt, model, actor_employee_profile_id, *, all_permission_held: bool)`) reused
by every Lead/Customer/child-record query — not reimplemented per-route, which is exactly how an IDOR
gap would otherwise creep in one endpoint at a time.

## Reassignment access policy (explicit, since the spec asks for a documented choice)

On reassignment: the previous assignee loses write access immediately; the previous assignee's
*read* access is retained only if they were also the creator (rule #1) — otherwise fully revoked. This
is the real, specific policy `record-ownership-policy.md` commits to (the governing instruction leaves
this an open documented choice — resolved here, not left ambiguous for the implementation to guess).

## Search and pagination must not leak existence

A search or list endpoint for an employee with only `_own` permission must apply the ownership filter
*before* pagination/counting — never return a total count or "no results due to permission" distinct
from "no results because none exist" (both must look identical: an empty, correctly-paginated result,
never a `403` that would confirm "the record exists but isn't yours" — see
`data-isolation-threat-model.md`).

## Audit records remain management-only

`audit.view` permission is unchanged (SUPER_ADMIN only, per the existing RBAC seed) — Lead/Customer
audit trails are never exposed through the employee-facing Lead/Customer detail endpoints, only through
the separate, already-permission-gated `/audit` UI.
