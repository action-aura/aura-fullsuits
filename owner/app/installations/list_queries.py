"""UI modernization Stage D.5 (Licensing Command Center) -- list-query
service for the Installations list screen, factored out of
installations/routes.py::list_installations's previously fully-unbounded
query (no LIMIT/OFFSET at all). See
docs/owner/ui-modernization/licensing-command-center-contract.md.

Installation has no ownership-shaped permission split -- installations.view/
register/update/suspend/replace_device are all flat, company-wide-once-held
codes (verified against app.staff.seed_data.py). No apply_ownership_filter()
call, matching the Payments/Refunds/Subscriptions/Licenses precedent.
"""
from __future__ import annotations

from sqlalchemy import Select, or_, select

from app.models.customers import Customer
from app.models.installations import Installation
from app.services.pagination import DEFAULT_PAGE_SIZE, paginate

INSTALLATION_SORT_COLUMNS = {
    "status": Installation.status,
    "first_registered_at": Installation.first_registered_at,
    "last_check_in_at": Installation.last_check_in_at,
    "created_at": Installation.created_at,
}


def _ordered(stmt: Select, sort: str, direction: str) -> Select:
    column = INSTALLATION_SORT_COLUMNS.get(sort, Installation.created_at)
    order = column.asc() if direction == "asc" else column.desc()
    return stmt.order_by(order, Installation.id)


def list_installations(
    *,
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
    status: str | None = None,
    search: str | None = None,
    sort: str = "created_at",
    direction: str = "desc",
) -> dict:
    """With no query params, reproduces the exact prior
    `select(Installation).order_by(Installation.created_at.desc())`
    behavior. Previously unbounded; now always paginated (page_size=25
    default).

    Installation has no document-number-shaped identifier column -- search
    covers the real, joined Customer.legal_name (already displayed on every
    row) OR the installation's own real, if sparse, `installation_label`/
    `device_label` free-text columns (both already shown on this list
    screen)."""
    stmt = select(Installation).join(Installation.customer)
    if status:
        stmt = stmt.where(Installation.status == status)
    if search:
        pattern = f"%{search.strip()}%"
        stmt = stmt.where(or_(
            Customer.legal_name.ilike(pattern),
            Installation.installation_label.ilike(pattern),
            Installation.device_label.ilike(pattern),
        ))
    stmt = _ordered(stmt, sort, direction)
    return paginate(stmt, page, page_size)
