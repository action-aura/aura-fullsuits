"""UI modernization Stage D (enterprise-table-system) -- list-query service
for the /operations/expenses screen, factored out of
operations_ui/routes.py's previously inline `.limit(200)` query, following
the exact pattern already established by
app.customers.services.list_customers / app.commercial_sales.list_queries
(real, opt-in ?page/?status/?q/?sort/?dir query params, paginate() from
app.services.pagination, apply_ownership_filter reused verbatim -- never
re-implemented -- for the ownership-scoped entity).

The /operations/expenses route previously had NO pagination at all -- just
an unconditional `.limit(200)` (operations_ui/routes.py) -- the exact same
disclosed performance risk enterprise-table-system.md found and fixed for
Customers/Leads and commercial-flow-ui-contract.md found and fixed for the
seven commercial-sales list screens. This module fixes it the same additive
way: with no query params, it reproduces the prior
`order_by(Expense.created_at.desc())` behavior, now with real, always-on
pagination (page_size=25, matching every other domain) instead of a bare
200-row cap."""
from __future__ import annotations

import uuid

from sqlalchemy import Select, or_, select

from app.leads.ownership import apply_ownership_filter
from app.models.expenses import Expense
from app.services.pagination import DEFAULT_PAGE_SIZE, paginate

# UI modernization Stage D -- the only columns a real, verified server-side
# ORDER BY exists for. Anything else (including no ?sort= at all) falls
# back to created_at, the exact prior unconditional ordering the route used.
EXPENSE_SORT_COLUMNS = {
    "expense_number": Expense.expense_number,
    "status": Expense.status,
    "amount": Expense.amount,
    "expense_date": Expense.expense_date,
    "created_at": Expense.created_at,
}


def list_expenses(
    *,
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
    status: str | None = None,
    search: str | None = None,
    sort: str = "created_at",
    direction: str = "desc",
    actor_employee_profile_id: uuid.UUID | None,
    all_permission_held: bool,
) -> dict:
    """Ownership scoping delegates entirely to the shared
    apply_ownership_filter() (Expense's creator-only rule, added there this
    pass) -- never a second, hand-rolled `entered_by_employee_profile_id ==`
    filter written here. `search` matches Expense.description (real,
    required Text column -- doubles as the "business purpose" field, no
    separate column) or Expense.external_reference (real, nullable String
    column) -- the only two real free-text fields on Expense; no
    expense-number ILIKE the way Quote/Order/Invoice get one, since
    expense_number is nullable (assigned only at submit time, DRAFT rows
    have none) and callers can already jump straight to a known expense via
    its detail URL."""
    stmt: Select = select(Expense)
    stmt = apply_ownership_filter(stmt, Expense, actor_employee_profile_id, all_permission_held=all_permission_held)
    if status:
        stmt = stmt.where(Expense.status == status)
    if search:
        like = f"%{search.strip()}%"
        stmt = stmt.where(or_(Expense.description.ilike(like), Expense.external_reference.ilike(like)))
    column = EXPENSE_SORT_COLUMNS.get(sort, Expense.created_at)
    order = column.asc() if direction == "asc" else column.desc()
    # Stable secondary sort on the real primary key -- matches
    # enterprise-table-system.md's Customers/Leads precedent, so two rows
    # sharing the exact same sorted value never have an unstable relative
    # page position.
    stmt = stmt.order_by(order, Expense.id)
    return paginate(stmt, page, page_size)
