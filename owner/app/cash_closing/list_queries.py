"""UI modernization Stage D (enterprise-table-system) -- list-query service
for the /operations/cash-closings screen, factored out of
operations_ui/routes.py's previously inline `.limit(60)` query.

Real, disclosed bug fixed here (finance-ui-contract.md's "cash closing
ownership" investigation): the prior query filtered ONLY by currency, no
ownership restriction at all, even though this route accepts
`cash_closing.view_own` as one of three sufficient permissions and that
permission's own seed_data.py label is literally "View cash closings this
account prepared". Any account holding only cash_closing.view_own (a real,
distinct permission from cash_closing.view_all -- both currently only ever
granted together to FINANCE, but the permission model plainly anticipates a
future narrower role) could see every closing company-wide, not just its
own -- a real over-exposure gap, not a deliberate design choice.

Ownership shape here does NOT fit app.leads.ownership.apply_ownership_filter
(reused verbatim everywhere it DOES apply -- never re-implemented for a
model it already covers): that helper's contract is EmployeeProfile-id
creator/assignee columns (Lead/Customer/Quote/SalesOrder/CommercialInvoice/
Expense); CashClosing's only actor column is
prepared_by_staff_user_id, a StaffUser id, with no separate assignee concept
at all -- a genuinely different shape, same "apply_ownership_filter
deliberately has no rule for this model" reasoning
commercial-flow-ui-contract.md already documents for
CommissionLedgerEntry's employee_profile_id-based (not creator/assignee)
split. This reproduces the same style directly, scoped to
prepared_by_staff_user_id, bypassed by cash_closing.view_all OR
cash_closing.approve (an approver reviews closings other preparers
submitted by definition of maker-checker -- restricting an approve-only
holder to their own prepared closings would make the permission
functionally useless; see operations_ui/routes.py::list_closings's own
comment) -- not a second parallel ownership mechanism, just the one this
model's real columns support. The identical bypass set is also applied to
closing_detail's own per-record ownership check (operations_ui/routes.py),
which had no ownership restriction at all before this pass -- a real
IDOR-shaped gap found during curl-verification, fixed alongside this
query."""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import Select, select

from app.models.cash_closing import CashClosing
from app.services.pagination import DEFAULT_PAGE_SIZE, paginate

CASH_CLOSING_SORT_COLUMNS = {
    "business_date": CashClosing.business_date,
    "status": CashClosing.status,
    "expected_closing_cash": CashClosing.expected_closing_cash,
    "variance": CashClosing.variance,
    "created_at": CashClosing.created_at,
}


def list_closings(
    *,
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
    currency: str = "USD",
    status: str | None = None,
    business_date: date | None = None,
    sort: str = "business_date",
    direction: str = "desc",
    actor_staff_user_id: uuid.UUID,
    view_all_held: bool,
) -> dict:
    """No `search` parameter -- CashClosing has no real free-text
    identifier column (variance_explanation/opening_cash_override_reason
    are sparse commentary fields, not identifiers; business_date + currency
    is how a closing is actually found, and both are already real filter/
    sort dimensions). No "no profile" branch is needed the way Expense's
    list needs one -- prepared_by_staff_user_id is a StaffUser id, always
    real and available for any authenticated actor, unlike an
    EmployeeProfile that might not exist yet.

    `business_date` (AUDIT-031) exists so
    app.api_operations.expenses_and_operations::cash_closings_route's JSON
    GET branch can delegate here instead of re-implementing ownership
    scoping inline -- that re-implementation is exactly how this query's
    own now-fixed IDOR gap (see the module docstring) got introduced in
    the first place, and how a second, undiscovered copy of the same bug
    sat live in the API blueprint for an entire phase (finance-ui-
    contract.md's own "not fixed -- lives in a different blueprint
    entirely" disclosure). Kept purely additive/keyword-only so the
    existing operations_ui/routes.py::list_closings caller is unaffected."""
    stmt: Select = select(CashClosing).where(CashClosing.currency == currency)
    if not view_all_held:
        stmt = stmt.where(CashClosing.prepared_by_staff_user_id == actor_staff_user_id)
    if status:
        stmt = stmt.where(CashClosing.status == status)
    if business_date:
        stmt = stmt.where(CashClosing.business_date == business_date)
    column = CASH_CLOSING_SORT_COLUMNS.get(sort, CashClosing.business_date)
    order = column.asc() if direction == "asc" else column.desc()
    stmt = stmt.order_by(order, CashClosing.id)
    return paginate(stmt, page, page_size)
