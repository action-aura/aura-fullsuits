# Phase 9.5A Milestone 2 — Bounded Context Map

Each context lists its real existing owner (if any) and what this phase adds.

## 1. Identity and Access

**Existing owner**: `owner/app/models/staff.py`, `owner/app/auth/`. Unchanged this phase except new
permission codes (Milestone 17).

## 2. Employee Operations

**New**: `owner/app/models/employees.py` (new). Depends on Identity and Access (`StaffUser`) — an
`EmployeeProfile` always has exactly one `StaffUser`, never the reverse dependency.

## 3. Sales Pipeline

**New**: `owner/app/models/leads.py` (new). Depends on Employee Operations (assignment/creation
attribution) and Identity and Access (permission checks). Produces a `Customer` row only through the
one canonical `LeadConversionService` — Customer Management never depends back on Sales Pipeline.

## 4. Customer Management

**Existing owner**: `owner/app/models/customers.py`. Extended with new child tables (locations —
Milestone 8) but the `Customer` row itself is unchanged. Depends on Employee Operations (ownership
fields already exist: `assigned_sales_staff_id`).

## 5. Commercial Catalog

**Existing owner**: `owner/app/models/catalog.py`. Unchanged structurally; Commercial Sales and
Licensing Operations both depend on it (read-only), it depends on nothing new.

## 6. Commercial Sales

**New**: `owner/app/models/commercial_sales.py` (new — quotes, orders, commercial invoices, refunds).
Depends on Customer Management, Commercial Catalog (price snapshots), Employee Operations (who sold
it). Reuses `PaymentRecord` (extended, additive FK) from the existing Subscriptions module rather than
introducing a new payment table.

## 7. Commissions

**New**: `owner/app/models/commissions.py` (new). Depends on Commercial Sales (an invoice/payment event
triggers eligibility evaluation) and Employee Operations (commission-plan assignment). Commercial Sales
never depends on Commissions (one-directional).

## 8. Commercial Expenses

**New**: `owner/app/models/expenses.py` (new). Depends on Employee Operations (entered-by/approved-by)
only. No dependency on Commercial Sales, Commissions, or Licensing — a genuinely standalone ledger
context, matching the spec's "not a full accounting ERP" framing.

## 9. Licensing Operations

**Existing owner**: `owner/app/models/subscriptions.py`, `licensing.py`, `installations.py`,
`activation_governance.py`, `owner/app/commercial_ops/device_slot_ops.py`. Extended with a new
**policy** layer (`device_policy_profiles`, etc. — Milestone 3) that sits above, never inside, the
existing enforcement authority. Depends on Commercial Catalog (a policy is attached to a `Plan`) and
Customer Management (a subscription belongs to a `Customer`).

## 10. Management Collaboration

**New**: `owner/app/models/management_notes.py` (new). Depends only on Identity and Access
(author/visibility) — deliberately has no foreign key into any other context's records (a management
note can *reference* a lead/customer/employee by UUID in free text or an optional nullable pointer, but
is never required to have one), keeping it a lightweight, independent context.

## 11. Reporting and Audit

**Existing owner**: `owner/app/audit/services.py` (Audit — extended with new `action_code`s only).
**New**: `owner/app/models/daily_reports.py` (Reporting — new). Reporting depends on (reads from) every
other context to compute its snapshot; no other context depends on Reporting. Audit is a cross-cutting
concern every context writes to, but Audit itself depends on nothing.

## Allowed dependency direction (enforced by code review convention, not a runtime framework)

```
Identity and Access
        ^
        |
Employee Operations
        ^
        |
   +----+----+------------------+
   |         |                  |
Sales     Customer          Expenses
Pipeline  Management            ^
   |         ^                  |
   +----> (LeadConversionService)
             |
             v
        Commercial Sales <---- Commercial Catalog
             |
             v
        Commissions

Licensing Operations <---- Commercial Catalog, Customer Management

Management Collaboration ---- (Identity and Access only)

Reporting and Audit <---- reads everything, nothing reads it back
```

UI routes never own domain logic (Non-Negotiable Principle 8 / Milestone 2's own instruction) — every
route calls a service function in the module for its own context; no route directly manipulates another
context's models.
