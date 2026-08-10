"""UI modernization Stage D.5 (Licensing Command Center) -- list-query
services for licensing_admin's two paginated list screens (Activation
requests, Device keys). Both previously used an unconditional `.limit(200)`
with no real page/offset support at all -- the same disclosed pattern
enterprise-table-system.md/commercial-flow-ui-contract.md/finance-ui-contract.md
already fixed elsewhere, now fixed here identically (page_size=25 default,
matching every other domain). See
docs/owner/ui-modernization/licensing-command-center-contract.md.

This blueprint is company-wide operational tooling (signing keys, device
keys, activation requests, offline policies) -- every permission code it
checks (activation_requests.view, device_keys.view, etc.) is flat, with no
_own/_all split anywhere in app.staff.seed_data.py, so neither function
below applies (or needs) any ownership filter -- company-wide-once-held,
same shape as every other list in this pass.
"""
from __future__ import annotations

from sqlalchemy import Select, select

from app.models.licensing_service import ActivationRequest, DevicePublicKey
from app.services.pagination import DEFAULT_PAGE_SIZE, paginate

ACTIVATION_REQUEST_SORT_COLUMNS = {
    "event_type": ActivationRequest.event_type,
    "result": ActivationRequest.result,
    "created_at": ActivationRequest.created_at,
}

DEVICE_KEY_SORT_COLUMNS = {
    "status": DevicePublicKey.status,
    "last_proof_at": DevicePublicKey.last_proof_at,
    "created_at": DevicePublicKey.created_at,
}


def list_activation_requests(
    *, page: int = 1, page_size: int = DEFAULT_PAGE_SIZE, event_type: str | None = None,
    sort: str = "created_at", direction: str = "desc",
) -> dict:
    """With no query params, reproduces the exact prior
    `select(ActivationRequest).order_by(created_at.desc())` ordering --
    previously capped at an unconditional 200 rows with no further page
    access at all; now always paginated."""
    stmt = select(ActivationRequest)
    if event_type:
        stmt = stmt.where(ActivationRequest.event_type == event_type)
    column = ACTIVATION_REQUEST_SORT_COLUMNS.get(sort, ActivationRequest.created_at)
    order = column.asc() if direction == "asc" else column.desc()
    stmt = stmt.order_by(order, ActivationRequest.id)
    return paginate(stmt, page, page_size)


def list_device_keys(
    *, page: int = 1, page_size: int = DEFAULT_PAGE_SIZE, status: str | None = None,
    sort: str = "created_at", direction: str = "desc",
) -> dict:
    """With no query params, reproduces the exact prior
    `select(DevicePublicKey).order_by(created_at.desc())` ordering --
    previously capped at an unconditional 200 rows; now always paginated.
    status filter is new/additive (the route had none before this pass) --
    the same real ACTIVE/REVOKED/REPLACED vocabulary
    device_key_status_label already covers."""
    stmt = select(DevicePublicKey)
    if status:
        stmt = stmt.where(DevicePublicKey.status == status)
    column = DEVICE_KEY_SORT_COLUMNS.get(sort, DevicePublicKey.created_at)
    order = column.asc() if direction == "asc" else column.desc()
    stmt = stmt.order_by(order, DevicePublicKey.id)
    return paginate(stmt, page, page_size)
