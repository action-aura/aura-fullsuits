"""Phase 5 prerequisite #3 -- paginated list query for the quarantine
console (quarantine_routes.py). Same shape as licensing_admin/list_queries.py's
list_device_keys: company-wide operational tooling, flat permission (no
_own/_all split), page_size=25 default."""
from __future__ import annotations

from sqlalchemy import select

from app.models.sync import SyncQuarantineEvent
from app.services.pagination import DEFAULT_PAGE_SIZE, paginate

QUARANTINE_SORT_COLUMNS = {
    "created_at": SyncQuarantineEvent.created_at,
    "status": SyncQuarantineEvent.status,
}


def list_quarantine_events(
    *, page: int = 1, page_size: int = DEFAULT_PAGE_SIZE, status: str | None = "PENDING",
    sort: str = "created_at", direction: str = "desc",
) -> dict:
    """Defaults to status="PENDING" -- an operator opening this screen wants
    to see what still needs attention first, not a wall of already-resolved
    history. Pass status=None explicitly for the unfiltered view."""
    stmt = select(SyncQuarantineEvent)
    if status:
        stmt = stmt.where(SyncQuarantineEvent.status == status)
    column = QUARANTINE_SORT_COLUMNS.get(sort, SyncQuarantineEvent.created_at)
    order = column.asc() if direction == "asc" else column.desc()
    stmt = stmt.order_by(order, SyncQuarantineEvent.id)
    return paginate(stmt, page, page_size)
