"""Phase 9.5A Milestone 22/7 -- shared ownership-filter helper.

One shared query-building helper reused by every Lead/Customer/child-record
query, per docs/owner/phase9_5a/record-ownership-policy.md's own explicit
instruction: "implemented as one shared query-building helper... reused ...
not reimplemented per-route, which is exactly how an IDOR gap would
otherwise creep in one endpoint at a time."

Ownership rule: an employee with only the `_own` permission variant may see
a record if they created it OR are the current assignee. A role holding the
`_all` variant skips the filter entirely (management/global-access roles).
"""
from __future__ import annotations

import uuid

from sqlalchemy import Select, or_, select

from app.models.commercial_sales import Quote, SalesOrder
from app.models.customers import Customer
from app.models.employees import EmployeeProfile
from app.models.leads import Lead


def apply_ownership_filter(
    stmt: Select,
    model: type,
    actor_employee_profile_id: uuid.UUID,
    *,
    all_permission_held: bool,
) -> Select:
    if all_permission_held:
        return stmt
    if model is Lead:
        return stmt.where(
            or_(
                Lead.created_by_employee_profile_id == actor_employee_profile_id,
                Lead.assigned_employee_profile_id == actor_employee_profile_id,
            )
        )
    if model is Customer:
        # Customer's own existing ownership column (assigned_sales_staff_id)
        # is a StaffUser id, not an EmployeeProfile id -- resolved via the
        # real 1:1 EmployeeProfile.staff_user_id link (see
        # lead-conversion-contract.md's own note on this exact FK-type
        # mismatch), never a second parallel ownership column.
        staff_id_subq = (
            select(EmployeeProfile.staff_user_id)
            .where(EmployeeProfile.id == actor_employee_profile_id)
            .scalar_subquery()
        )
        return stmt.where(Customer.assigned_sales_staff_id == staff_id_subq)
    if model is Quote:
        # Phase 9.5D -- Quote has no separate assignee concept (unlike
        # Lead), only a creator (created_by_employee_profile_id); ownership
        # is creator-only. See docs/owner/phase9_5d/quote-domain-contract.md.
        return stmt.where(Quote.created_by_employee_profile_id == actor_employee_profile_id)
    if model is SalesOrder:
        # Phase 9.5D Milestone 8 -- same creator-only rule as Quote (no
        # separate assignee concept on SalesOrder either).
        return stmt.where(SalesOrder.created_by_employee_profile_id == actor_employee_profile_id)
    raise NotImplementedError(f"apply_ownership_filter has no rule for {model!r}")
