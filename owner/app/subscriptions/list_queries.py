"""UI modernization Stage D.5 (Licensing Command Center) -- list-query
service for the Subscriptions list screen, factored out of
subscriptions/routes.py::list_subscriptions's previously fully-unbounded
query (no LIMIT/OFFSET at all -- not even the .limit(200) pattern already
fixed elsewhere for Commercial Sales/Expenses/Cash Closing, a real,
disclosed performance risk). See
docs/owner/ui-modernization/licensing-command-center-contract.md.

Subscription has no ownership-shaped permission split -- subscriptions.view/
create/update/renew/cancel/suspend are all flat, company-wide-once-held
codes (verified against app.staff.seed_data.py: no subscriptions.view_own/
view_all variant exists anywhere). This list is company-wide once
subscriptions.view is held, the same shape as Payments/Refunds in the
commercial-sales pass -- no apply_ownership_filter() call, matching that
precedent exactly (never inventing an ownership dimension the permission
model doesn't have).
"""
from __future__ import annotations

from sqlalchemy import Select, select

from app.models.customers import Customer
from app.models.subscriptions import Subscription
from app.services.pagination import DEFAULT_PAGE_SIZE, paginate

# The only columns a real, verified server-side ORDER BY exists for.
SUBSCRIPTION_SORT_COLUMNS = {
    "status": Subscription.status,
    "end_date": Subscription.end_date,
    "renewal_date": Subscription.renewal_date,
    "created_at": Subscription.created_at,
}


def _ordered(stmt: Select, sort: str, direction: str) -> Select:
    column = SUBSCRIPTION_SORT_COLUMNS.get(sort, Subscription.created_at)
    order = column.asc() if direction == "asc" else column.desc()
    # Stable secondary sort on the real primary key (enterprise-table-system.md precedent) --
    # two subscriptions sharing an identical sort value would otherwise have
    # an unstable relative page position.
    return stmt.order_by(order, Subscription.id)


def list_subscriptions(
    *,
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
    status: str | None = None,
    search: str | None = None,
    sort: str = "created_at",
    direction: str = "desc",
) -> dict:
    """With no query params (the old route's only call shape), reproduces the
    exact prior `select(Subscription).order_by(Subscription.created_at.desc())`
    behavior -- search/sort/pagination are strictly additive, opt-in via new
    query params. Previously the route had NO LIMIT/OFFSET at all; it is now
    always paginated (page_size=25 default, matching every other domain).

    Subscription has no subscription_number-shaped identifier column (unlike
    Quote/Order/Invoice's own document-number columns) -- search is over the
    real, joined Customer.legal_name instead, the same real identifier every
    existing subscriptions/list.html row already displays. The join is a
    plain inner join on a real, NOT NULL FK (Subscription.customer_id), so it
    never drops a row a caller-less query would otherwise return.
    """
    stmt = select(Subscription).join(Subscription.customer)
    if status:
        stmt = stmt.where(Subscription.status == status)
    if search:
        stmt = stmt.where(Customer.legal_name.ilike(f"%{search.strip()}%"))
    stmt = _ordered(stmt, sort, direction)
    return paginate(stmt, page, page_size)
