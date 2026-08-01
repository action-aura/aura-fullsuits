# Phase 9.5C — Milestone 3: Assignment and Reassignment Contract

## Lead (`app.leads.services.assign_lead`)

Requirements, all enforced in the service function itself:

| Requirement | Enforcement |
|---|---|
| Management/assign permission | `leads.assign` — checked at the route layer (Milestone 16), not inside the service (Non-Negotiable Rule 10: service stays request-independent). |
| Destination employee ACTIVE | `EmployeeProfile.employment_status == "ACTIVE"`, else `LeadError("DESTINATION_EMPLOYEE_NOT_ACTIVE")`. |
| Destination account usable | Same check — a `PENDING`/`SUSPENDED`/`TERMINATED` profile is rejected identically; there is no separate "usable" concept beyond `employment_status` in the existing model. |
| Reason | Required only when closing an *existing* open assignment (a first-ever assignment on lead creation doesn't require one); `LeadError("REASON_REQUIRED_FOR_REASSIGN")` otherwise. |
| Optimistic version | `expected_version` param, checked against `lead.version` before any mutation; `LeadError("STALE_LEAD_VERSION")` on mismatch. |
| Assignment-history row | `LeadAssignment` — existing close/open pattern, now also stores `reason` (additive column, this milestone). |
| Audit event | `LEAD_ASSIGNED` (first assignment) / `LEAD_REASSIGNED` (closing an existing one), now includes `reason`. |
| No historical creator rewrite | `Lead.created_by_employee_profile_id` never touched. |
| Transaction safety | Single `db_session.commit()` — LeadAssignment insert + `Lead.assigned_employee_profile_id` update + version bump are atomic. |

## Customer (`app.customers.services.assign_customer`) — new this milestone

Mirrors `assign_lead` exactly, adapted for Customer's existing `StaffUser`-typed
`assigned_sales_staff_id` (not an `EmployeeProfile` id):

| Requirement | Enforcement |
|---|---|
| Destination account usable | `StaffUser.is_active is True` AND `disabled_at IS NULL` — the Customer-domain equivalent of the Lead check, using the fields that actually exist on `StaffUser`. |
| Reason | Same rule — required only when closing an existing assignment. |
| Optimistic version | New `Customer.version` column (additive, this milestone) — Customer previously had no optimistic-lock field at all. |
| Assignment-history row | New `CustomerAssignment` table (additive, this milestone) — Customer previously had **no** assignment-history table; only the live `assigned_sales_staff_id` pointer existed with no historical record of prior assignments. Modeled as an exact structural mirror of `LeadAssignment`. |
| Audit event | `CUSTOMER_ASSIGNED` / `CUSTOMER_REASSIGNED`. |

## Termination/suspension integration

- A suspended/terminated employee's `EmployeeProfile.employment_status`
  is no longer `ACTIVE` — both `assign_lead`/`assign_customer` already
  refuse to assign *to* such an employee (see above).
- Records **already** assigned to a since-suspended employee are not
  automatically reassigned — no destructive automatic action, per the
  spec's explicit instruction. A "reassignment-required" indicator for
  management (Milestone 14's dashboard: "suspended/terminated employee
  records requiring reassignment") is a query, not a data mutation —
  deferred to the dashboard milestone, not implemented as an automatic
  side effect of suspension.
- Phase 9.5B session/lifecycle behavior (login blocking, session
  revocation) is completely unchanged — this milestone touches only the
  CRM-ownership layer, never `app/employees/services.py`'s suspension
  logic itself.

## Bulk reassignment

Explicitly out of scope for this milestone (no bulk endpoint exists yet).
When built (a later milestone), it must reuse `assign_lead`/
`assign_customer` per-record inside one transaction, never a separate
bulk-only code path — consistent with "optional bulk reassignment must
be explicit, transactional, and audited."
