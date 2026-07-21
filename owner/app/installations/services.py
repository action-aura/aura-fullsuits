"""Installation, device, and activation-event services (Part P/Q)."""
from __future__ import annotations

from app.audit.services import record as audit_record
from app.extensions import db_session
from app.models.base import utcnow
from app.models.installations import ActivationEvent, DeviceRecord, Installation, InstallationStatusHistory

VALID_TRANSITIONS: dict[str, set[str]] = {
    "REGISTERED": {"PENDING_ACTIVATION", "ACTIVE", "SUSPENDED", "DEACTIVATED"},
    "PENDING_ACTIVATION": {"ACTIVE", "DEACTIVATED"},
    "ACTIVE": {"SUSPENDED", "DEACTIVATED", "REPLACED"},
    "SUSPENDED": {"ACTIVE", "DEACTIVATED"},
    "DEACTIVATED": set(),
    "REPLACED": set(),
}

EVENT_TYPES = (
    "ACTIVATION_REQUESTED", "ACTIVATION_APPROVED", "ACTIVATION_REJECTED",
    "DEVICE_REGISTERED", "DEVICE_REPLACED",
    "LICENSE_SUSPENDED", "LICENSE_REACTIVATED", "LICENSE_EXPIRED", "LICENSE_REVOKED",
    "CHECK_IN_RECORDED", "OFFLINE_GRACE_STARTED", "OFFLINE_GRACE_ENDED",
    "DEACTIVATION_ACCEPTED",  # Phase 6, Part B
)


class InvalidInstallationTransitionError(ValueError):
    pass


def register_installation(fields: dict, actor_staff_user_id) -> Installation:
    installation = Installation(status="REGISTERED", first_registered_at=utcnow(), **fields)
    db_session.add(installation)
    db_session.commit()
    record_activation_event(
        installation, "DEVICE_REGISTERED", "SUCCESS", actor_staff_user_id, reason_code=None, correlation_id=None
    )
    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="INSTALLATION_REGISTERED",
        entity_type="installation",
        entity_public_id=str(installation.id),
        after_state={"status": installation.status, "product_id": str(installation.product_id)},
    )
    return installation


def transition_installation(installation: Installation, to_status: str, actor_staff_user_id, reason: str | None = None) -> None:
    allowed = VALID_TRANSITIONS.get(installation.status, set())
    if to_status not in allowed:
        raise InvalidInstallationTransitionError(f"Cannot transition installation from {installation.status} to {to_status}.")
    from_status = installation.status
    installation.status = to_status
    if to_status == "DEACTIVATED":
        installation.deactivated_at = utcnow()
    db_session.add(
        InstallationStatusHistory(
            installation_id=installation.id, from_status=from_status, to_status=to_status,
            changed_by_staff_user_id=actor_staff_user_id, reason=reason,
        )
    )
    db_session.commit()
    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="INSTALLATION_STATUS_CHANGED",
        entity_type="installation",
        entity_public_id=str(installation.id),
        before_state={"status": from_status},
        after_state={"status": to_status},
        reason=reason,
    )


def record_activation_event(
    installation: Installation | None, event_type: str, result: str, actor_staff_user_id,
    reason_code: str | None = None, correlation_id: str | None = None, license_id=None, safe_metadata: dict | None = None,
) -> ActivationEvent:
    if event_type not in EVENT_TYPES:
        raise ValueError(f"Unknown activation event type: {event_type}")
    event = ActivationEvent(
        installation_id=installation.id if installation else None,
        license_id=license_id or (installation.license_id if installation else None),
        event_type=event_type,
        result=result,
        reason_code=reason_code,
        correlation_id=correlation_id,
        originating_staff_user_id=actor_staff_user_id,
        safe_metadata=safe_metadata or {},
    )
    db_session.add(event)
    db_session.commit()
    return event


def register_device(installation: Installation, device_label: str | None, fingerprint_hash: str | None, actor_staff_user_id) -> DeviceRecord:
    device = DeviceRecord(
        installation_id=installation.id, device_label=device_label, fingerprint_hash=fingerprint_hash, registered_at=utcnow()
    )
    db_session.add(device)
    db_session.commit()
    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="DEVICE_REGISTERED",
        entity_type="installation",
        entity_public_id=str(installation.id),
    )
    return device
