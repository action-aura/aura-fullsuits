# Phase 9.5A Milestone 7 — Data Isolation Threat Model

## Threats and mitigations

| Threat | Mitigation |
|---|---|
| Direct UUID lookup of another employee's lead/customer | `apply_ownership_filter()` applied to every single-record `GET` — a record outside the actor's ownership resolves as `RECORD_NOT_FOUND` (not `PERMISSION_DENIED`) so existence itself isn't confirmed (see below) |
| Search returning another employee's records | Filter applied before the query executes, not post-filtered in Python (avoids both the leak and the performance/pagination-correctness bug of filtering after `LIMIT`) |
| Pagination/count leaking existence | Count queries use the same filtered statement as the list query — never a separate unfiltered `COUNT(*)` |
| Notes/locations/interactions/follow-ups (child records) accessed by parent UUID bypass | Every child-record query joins back to the parent (Lead/Customer) and re-applies the same ownership filter — a child record is never independently queryable by its own UUID without the parent check |
| Reassignment used to silently grant/revoke access without audit | Every `lead_assignments`/`Customer.assigned_sales_staff_id` change is a `LEAD_REASSIGNED`/similar audit event (Milestone 23) |
| Management-only notes/fields exposed to employee-facing serializers | Response schemas (Milestone 9's view models) are role-parameterized at the serialization layer — a field like `EmployeeProfile.notes` (management-only) is simply absent from the JSON dict built for a non-management caller, not merely hidden by the UI |
| Commission data cross-leakage via a shared Lead/Customer/Sale record | Commission ledger entries (Milestone 12) carry their own `employee_profile_id` and are filtered independently — never inferred from "who can see this lead" |
| Two management accounts (Bahaa/Awab) sharing data but blurring attribution | Every write's audit event always carries the real `actor_staff_user_id` of whichever account performed it — shared visibility, never shared identity |

## `RECORD_NOT_FOUND` vs `PERMISSION_DENIED` — the specific anti-enumeration decision

For Lead/Customer and their children: a record that exists but is outside the caller's ownership
returns the *same* `RECORD_NOT_FOUND` (404-equivalent) as a record that doesn't exist at all — never
`PERMISSION_DENIED`, which would confirm existence to an employee probing UUIDs they don't own. This
differs from action-level permission checks (e.g. attempting `leads.convert` without that permission at
all, regardless of which lead) which correctly return `PERMISSION_DENIED` — the distinction is
*record-level ownership* (hide existence) vs *action-level capability* (fine to reveal "you can't do
this at all").

## Required tests (real, added at Milestone 24)

Employee A cannot view/update/list-infer/access-children-of Employee B's lead or customer; reassignment
behaves per the documented policy; management sees all; two management accounts see identical data with
distinct audit attribution.
