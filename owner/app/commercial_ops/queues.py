"""Role-based operational queues (Phase 8 Part S, Milestone 6).

A pure read/aggregation layer over tables that already exist -- this
module never creates, transitions, or decides anything itself, it only
answers "what does role X need to look at right now." Returns plain dicts
(not ORM objects) so a future route/UI layer (Milestone 7's "full internal
Owner UI workflow set") can serialize the result directly without a
translation layer, the same reasoning `dashboard/services.py` already
follows for its own summary dict.

`VIEWER` gets counts only, never actionable items -- the role has no
permission to act on any of these, so listing them would be dead weight at
best and a source of confusion at worst ("why can I see this but not touch
it").
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select

from app.extensions import db_session
from app.models.activation_governance import DeviceSlotException, PendingActivation
from app.models.commercial_ops import EmergencyExtension, InternalNotification, PilotRecord, RenewalRequest

QUEUE_ROLE_CODES = ("SALES", "FINANCE", "SUPPORT", "SUPER_ADMIN", "VIEWER")

_SALES_RENEWAL_STATUSES = ("DRAFT", "QUOTED", "AWAITING_CONFIRMATION", "AWAITING_PAYMENT")
_FINANCE_RENEWAL_STATUSES = ("PAYMENT_RECORDED",)
_PILOT_ACTIONABLE_STATUSES = ("DRAFT", "APPROVED")


class UnknownQueueRoleError(ValueError):
    pass


@dataclass
class QueueSnapshot:
    role_code: str
    items: dict[str, list[dict]] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)


def _notification_items(*, role_code: str | None) -> list[dict]:
    stmt = select(InternalNotification).where(InternalNotification.status.in_(("OPEN", "IN_PROGRESS")))
    if role_code is not None:
        stmt = stmt.where(InternalNotification.assigned_role_code == role_code)
    rows = db_session.execute(stmt.order_by(InternalNotification.created_at.asc())).scalars().all()
    return [
        {"id": str(n.id), "type": n.notification_type, "severity": n.severity, "title": n.title, "status": n.status}
        for n in rows
    ]


def _renewal_items(statuses: tuple[str, ...]) -> list[dict]:
    rows = db_session.execute(
        select(RenewalRequest).where(RenewalRequest.status.in_(statuses)).order_by(RenewalRequest.created_at.asc())
    ).scalars().all()
    return [
        {"id": str(r.id), "subscription_id": str(r.subscription_id), "status": r.status,
         "proposed_term_end": r.proposed_term_end.isoformat() if r.proposed_term_end else None}
        for r in rows
    ]


def _pilot_items() -> list[dict]:
    rows = db_session.execute(
        select(PilotRecord).where(PilotRecord.status.in_(_PILOT_ACTIONABLE_STATUSES)).order_by(PilotRecord.created_at.asc())
    ).scalars().all()
    return [{"id": str(p.id), "subscription_id": str(p.subscription_id), "status": p.status} for p in rows]


def _pending_activation_items() -> list[dict]:
    rows = db_session.execute(
        select(PendingActivation).where(PendingActivation.status == "PENDING_REVIEW").order_by(PendingActivation.created_at.asc())
    ).scalars().all()
    return [{"id": str(p.id), "installation_id": str(p.installation_id), "mode": p.mode} for p in rows]


def _emergency_extension_items() -> list[dict]:
    rows = db_session.execute(
        select(EmergencyExtension).where(EmergencyExtension.status == "ACTIVE").order_by(EmergencyExtension.created_at.asc())
    ).scalars().all()
    return [
        {"id": str(e.id), "subscription_id": str(e.subscription_id), "expires_at": e.expires_at.isoformat()}
        for e in rows
    ]


def _device_slot_exception_items() -> list[dict]:
    rows = db_session.execute(
        select(DeviceSlotException).where(DeviceSlotException.status == "ACTIVE").order_by(DeviceSlotException.created_at.asc())
    ).scalars().all()
    return [
        {"id": str(e.id), "license_id": str(e.license_id), "extra_slots": e.extra_slots, "expires_at": e.expires_at.isoformat()}
        for e in rows
    ]


def get_queue_for_role(role_code: str) -> QueueSnapshot:
    if role_code not in QUEUE_ROLE_CODES:
        raise UnknownQueueRoleError(f"Unknown queue role: {role_code}")

    if role_code == "VIEWER":
        counts = {
            "notifications_open": len(_notification_items(role_code=None)),
            "renewals_in_sales_pipeline": len(_renewal_items(_SALES_RENEWAL_STATUSES)),
            "renewals_awaiting_finance_approval": len(_renewal_items(_FINANCE_RENEWAL_STATUSES)),
            "pilots_needing_action": len(_pilot_items()),
            "pending_activations": len(_pending_activation_items()),
            "active_emergency_extensions": len(_emergency_extension_items()),
            "active_device_slot_exceptions": len(_device_slot_exception_items()),
        }
        return QueueSnapshot(role_code=role_code, items={}, counts=counts)

    if role_code == "SUPER_ADMIN":
        items = {
            "notifications": _notification_items(role_code=None),
            "renewals_in_sales_pipeline": _renewal_items(_SALES_RENEWAL_STATUSES),
            "renewals_awaiting_finance_approval": _renewal_items(_FINANCE_RENEWAL_STATUSES),
            "pilots_needing_action": _pilot_items(),
            "pending_activations": _pending_activation_items(),
            "active_emergency_extensions": _emergency_extension_items(),
            "active_device_slot_exceptions": _device_slot_exception_items(),
        }
    elif role_code == "SALES":
        items = {
            "notifications": _notification_items(role_code="SALES"),
            "renewals_in_sales_pipeline": _renewal_items(_SALES_RENEWAL_STATUSES),
            "pilots_needing_action": _pilot_items(),
        }
    elif role_code == "FINANCE":
        items = {
            "notifications": _notification_items(role_code="FINANCE"),
            "renewals_awaiting_finance_approval": _renewal_items(_FINANCE_RENEWAL_STATUSES),
        }
    else:  # SUPPORT
        items = {
            "notifications": _notification_items(role_code="SUPPORT"),
            "pending_activations": _pending_activation_items(),
            "active_emergency_extensions": _emergency_extension_items(),
            "active_device_slot_exceptions": _device_slot_exception_items(),
        }

    counts = {key: len(value) for key, value in items.items()}
    return QueueSnapshot(role_code=role_code, items=items, counts=counts)
