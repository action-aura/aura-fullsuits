# Phase 9.5C — Milestone 3: CRM Record Ownership Contract

## Real, significant finding: the live Customer route had no data isolation

Before this milestone, `customers/routes.py`'s `list_customers()` and
`detail()` were gated only by the plain `customers.view` permission —
seeded identically to SALES, SUPPORT, FINANCE, and VIEWER — with **zero**
ownership filtering. Every employee holding `customers.view` could list
and open **every** Customer record by UUID, regardless of assignment.
The newer `customers.view_own`/`customers.view_all` permissions Phase
9.5A had already seeded were never actually read by any route — inert.

This is a real violation of Non-Negotiable Domain Rule 3 that predates
this phase; fixing it is explicitly this milestone's job ("implement the
Phase 9.5A record-ownership policy concretely... the actual foundation
contract is authoritative").

## Distinction: `customers.view` (gate) vs `customers.view_own`/`view_all` (scope)

Confirmed from `app/staff/seed_data.py`: every role that has any Customer
visibility holds plain `customers.view` **together with** one of
`_own`/`_all`. This is a coherent, intentional two-layer design once
read correctly: `customers.view` gates entry to the `/customers` area at
all; `_own`/`_all` decides the *scope* once inside. No permission
renaming was needed — only wiring the scope check that was already
seeded but never read.

## Fix applied

- `list_customers()`: now resolves the actor's permission codes; if
  `customers.view_all` is absent, applies `apply_ownership_filter()` (the
  same shared helper Lead queries use) before executing the query.
- `detail()`, `update()`, `archive()`, `add_contact_route()`,
  `add_note_route()`: now call a new shared `_customer_visible_to()`
  record-level check (view_all, OR `customer.assigned_sales_staff_id ==
  actor.id`) before proceeding — closes a real IDOR (any employee with
  `customers.view` could previously open/edit/archive/note any customer
  by guessing or enumerating its UUID).
- `create_customer()`: now defaults `assigned_sales_staff_id` to the
  creating actor when the caller doesn't supply one. Without this, a
  customer created through the existing route (which never set an
  assignee) would become invisible to its own creator the moment
  ownership filtering was enforced — verified by the fact that all 4
  pre-existing `test_customers.py` tests create-then-immediately-view a
  customer as the same actor, and continued passing only once this
  default was added.

## Distinguishing creator / owner / assignee (Customer)

Unlike `Lead` (which has both `created_by_employee_profile_id` and
`assigned_employee_profile_id`), `Customer` has no separate "creator"
concept — only `assigned_sales_staff_id`. This is an accepted, existing
limitation of the Phase-5-era `Customer` model, not something this
milestone changes (Non-Negotiable Domain Rule 2: do not create a second
Customer authority). The practical effect: for a Customer, "creator
access survives reassignment" is **not applicable** — a Customer's
access is determined solely by current assignment (or `view_all`), by
design of the existing schema. Documented explicitly, not left implicit.

For `Lead`, creator access **does** survive reassignment —
`apply_ownership_filter()`'s `Lead` branch already checks `created_by
OR assigned`, unchanged by this milestone.

## Management visibility

Any role holding `customers.view_all` / `leads.view_all` (FINANCE,
VIEWER, and SUPER_ADMIN via its wildcard) is unaffected — sees every
record, exactly as before. This satisfies "Bahaa and Awab must use
separate authenticated management accounts but see the same
authoritative records" without hardcoding either name — both simply need
a role holding the `_all` permission.

## No historical creator rewrite

Confirmed: `assign_lead()`/`assign_customer()` only ever mutate the
*current* pointer (`Lead.assigned_employee_profile_id` /
`Customer.assigned_sales_staff_id`) and append a new history row;
`Lead.created_by_employee_profile_id` is never touched by either
function.
