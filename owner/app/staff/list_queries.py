"""UI modernization Stage D.6 (Employee/Staff/Role-Assignment) -- list-query
service for the Staff Accounts list screen, factored out of
staff/routes.py::list_staff's previously fully-unbounded query (no
LIMIT/OFFSET at all -- every staff account row was fetched on every page
load). Same disclosed performance-risk class already fixed for
Subscriptions/Licenses/Installations in Stage D.5
(licensing-command-center-contract.md); fixed here identically. See
docs/owner/ui-modernization/employee-permission-ui-contract.md.

StaffUser has no ownership-shaped permission split -- staff.view/create/
update/disable/assign_roles/reset_mfa are all flat, company-wide-once-held
codes (verified against app.staff.seed_data.py: no staff.view_own/
staff.view_all variant exists anywhere -- unlike employees.view_own/
view_all, which DOES have a real split but is investigated separately,
see the contract doc). This list is company-wide once staff.view is held,
the same shape as Subscriptions/Licenses/Installations in Stage D.5 -- no
apply_ownership_filter() call, matching that precedent exactly.
"""
from __future__ import annotations

from sqlalchemy import Select, and_, or_, select

from app.models.staff import StaffUser
from app.services.pagination import DEFAULT_PAGE_SIZE, paginate

# The only columns a real, verified server-side ORDER BY exists for.
STAFF_SORT_COLUMNS = {
    "display_name": StaffUser.display_name,
    "email": StaffUser.email,
    "created_at": StaffUser.created_at,
}


def _ordered(stmt: Select, sort: str, direction: str) -> Select:
    column = STAFF_SORT_COLUMNS.get(sort, StaffUser.created_at)
    order = column.asc() if direction == "asc" else column.desc()
    # Stable secondary sort on the real primary key (enterprise-table-system.md
    # precedent) -- two accounts sharing an identical sort value would
    # otherwise have an unstable relative page position.
    return stmt.order_by(order, StaffUser.id)


def list_staff_accounts(
    *,
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
    status: str | None = None,
    search: str | None = None,
    sort: str = "created_at",
    direction: str = "desc",
) -> dict:
    """With no query params (the old route's only call shape), reproduces the
    exact prior `select(StaffUser).order_by(StaffUser.created_at.desc())`
    behavior -- search/status/sort/pagination are strictly additive, opt-in
    via new query params. Previously the route had NO LIMIT/OFFSET at all;
    it is now always paginated (page_size=25 default, matching every other
    domain).

    StaffUser has no dedicated status column -- `status` here filters on the
    exact real (is_active AND disabled_at IS NULL) vs. not condition
    staff/list.html's/detail.html's own pre-existing badge ternary already
    used to decide "Active" vs. "Disabled" (`{% if s.is_active and not
    s.disabled_at %}`), reproduced here at the query layer, not a new
    derived state invented for this pass.

    Search is over the real, always-displayed `display_name`/`email`
    columns -- the same two identifier fields every existing staff/list.html
    row already showed.
    """
    stmt = select(StaffUser)
    if status == "ACTIVE":
        stmt = stmt.where(and_(StaffUser.is_active.is_(True), StaffUser.disabled_at.is_(None)))
    elif status == "DISABLED":
        stmt = stmt.where(or_(StaffUser.is_active.is_(False), StaffUser.disabled_at.is_not(None)))
    if search:
        like = f"%{search.strip()}%"
        stmt = stmt.where(or_(StaffUser.display_name.ilike(like), StaffUser.email.ilike(like)))
    stmt = _ordered(stmt, sort, direction)
    return paginate(stmt, page, page_size)
