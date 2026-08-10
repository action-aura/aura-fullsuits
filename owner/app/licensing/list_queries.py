"""UI modernization Stage D.5 (Licensing Command Center) -- list-query
service for the Licenses list screen, factored out of
licensing/routes.py::list_licenses's previously fully-unbounded query (no
LIMIT/OFFSET at all). See
docs/owner/ui-modernization/licensing-command-center-contract.md.

License has no ownership-shaped permission split -- licenses.view/create/
issue/suspend/revoke/replace/reactivate are all flat, company-wide-once-held
codes (verified against app.staff.seed_data.py). No apply_ownership_filter()
call, matching the Payments/Refunds/Subscriptions precedent.
"""
from __future__ import annotations

from sqlalchemy import Select, or_, select

from app.models.customers import Customer
from app.models.licensing import License
from app.services.pagination import DEFAULT_PAGE_SIZE, paginate

LICENSE_SORT_COLUMNS = {
    "status": License.status,
    "issued_at": License.issued_at,
    "valid_until": License.valid_until,
    "created_at": License.created_at,
}


def _ordered(stmt: Select, sort: str, direction: str) -> Select:
    column = LICENSE_SORT_COLUMNS.get(sort, License.created_at)
    order = column.asc() if direction == "asc" else column.desc()
    return stmt.order_by(order, License.id)


def list_licenses(
    *,
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
    status: str | None = None,
    search: str | None = None,
    sort: str = "created_at",
    direction: str = "desc",
) -> dict:
    """With no query params, reproduces the exact prior
    `select(License).order_by(License.created_at.desc())` behavior --
    search/sort/pagination are strictly additive. Previously unbounded; now
    always paginated (page_size=25 default).

    License has no license_number-shaped identifier column -- search covers
    the real, joined Customer.legal_name (same identifier every existing row
    already displays) OR the license's own real key_prefix ("safe to display
    always" per app.models.licensing.License's own column comment -- never
    the HMAC secret or masked suffix, only the real, non-secret prefix e.g.
    'AURA-CLN-1', a genuinely useful real-world search target for support
    staff who already have a customer-quoted prefix)."""
    stmt = select(License).join(License.customer)
    if status:
        stmt = stmt.where(License.status == status)
    if search:
        pattern = f"%{search.strip()}%"
        stmt = stmt.where(or_(Customer.legal_name.ilike(pattern), License.key_prefix.ilike(pattern)))
    stmt = _ordered(stmt, sort, direction)
    return paginate(stmt, page, page_size)
