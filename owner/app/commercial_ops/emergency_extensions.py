"""Emergency extensions (Phase 8 Part N).

NOT a bypass -- see EmergencyExtension's own docstring
(app/models/commercial_ops.py). Never touches Subscription/License/
RenewalRecord rows, never marks a payment confirmed. Permission
(`emergency_extensions.create`/`.revoke`) and MFA/recent-auth enforcement
happen at the ROUTE layer (not here) -- require_recent_auth needs a Flask
request context a pure service function does not have. This module only
enforces the invariants that hold regardless of caller: hard time cap, no
stacking, reason required, never usable to override a REVOKED license.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from app.audit.services import record as audit_record
from app.extensions import db_session
from app.models.base import utcnow
from app.models.commercial_ops import EmergencyExtension
from app.models.licensing import License
from app.models.subscriptions import Subscription

MAX_EMERGENCY_EXTENSION_HOURS = 72


class EmergencyExtensionError(ValueError):
    pass


def _active_extension_for(subscription_id, now: datetime) -> EmergencyExtension | None:
    return (
        db_session.query(EmergencyExtension)
        .filter(
            EmergencyExtension.subscription_id == subscription_id,
            EmergencyExtension.status == "ACTIVE",
            EmergencyExtension.expires_at > now,
        )
        .first()
    )


def create_emergency_extension(
    *,
    subscription: Subscription,
    reason: str,
    duration_hours: int,
    actor_staff_user_id,
    license: License | None = None,
    incident_reference: str | None = None,
    device_scope: list | None = None,
    now: datetime | None = None,
) -> EmergencyExtension:
    if not reason or not reason.strip():
        raise EmergencyExtensionError("A reason is required to create an emergency extension.")
    if duration_hours <= 0 or duration_hours > MAX_EMERGENCY_EXTENSION_HOURS:
        raise EmergencyExtensionError(
            f"duration_hours must be between 1 and {MAX_EMERGENCY_EXTENSION_HOURS} (explicit, short-lived only)."
        )
    if license is not None and license.status == "REVOKED":
        raise EmergencyExtensionError("Cannot create an emergency extension for a REVOKED license.")

    # Phase 8V-P5: found by real physical validation -- this used to default to
    # the naive, deprecated datetime.utcnow() (a UTC *value* with no tzinfo).
    # Stored into starts_at/expires_at (both DateTime(timezone=True) columns),
    # a naive value gets silently localized using the DB session's own
    # timezone setting rather than being treated as UTC -- on a non-UTC
    # session (this project's real dev Postgres runs Asia/Amman, +3) that
    # shifted every extension's real stored window a full 3 hours earlier
    # than intended, so a short-duration extension could already be expired
    # by the time it was created. app.models.base.utcnow() (timezone-aware,
    # used correctly everywhere else in this codebase) fixes it.
    now = now or utcnow()
    # v1 simplification (Part N): no stacking -- one ACTIVE, unexpired
    # emergency extension per subscription at a time. A new need supersedes
    # via explicit revoke-then-recreate, never silent overlap.
    existing = _active_extension_for(subscription.id, now)
    if existing is not None:
        raise EmergencyExtensionError(
            f"Subscription already has an active emergency extension (id={existing.id}) until {existing.expires_at}."
        )

    extension = EmergencyExtension(
        subscription_id=subscription.id,
        license_id=license.id if license else None,
        reason=reason,
        incident_reference=incident_reference,
        starts_at=now,
        expires_at=now + timedelta(hours=duration_hours),
        device_scope=device_scope,
        status="ACTIVE",
        created_by_staff_user_id=actor_staff_user_id,
    )
    db_session.add(extension)
    db_session.commit()
    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="EMERGENCY_EXTENSION_CREATED",
        entity_type="emergency_extension",
        entity_public_id=str(extension.id),
        after_state={
            "subscription_id": str(subscription.id),
            "expires_at": extension.expires_at.isoformat(),
            "duration_hours": duration_hours,
        },
        reason=reason,
    )
    return extension


def revoke_emergency_extension(
    extension: EmergencyExtension, *, reason: str, actor_staff_user_id, now: datetime | None = None
) -> None:
    if extension.status != "ACTIVE":
        raise EmergencyExtensionError(f"Cannot revoke an emergency extension in status {extension.status}.")
    if not reason or not reason.strip():
        raise EmergencyExtensionError("A reason is required to revoke an emergency extension.")

    now = now or utcnow()
    extension.status = "REVOKED"
    extension.revoked_at = now
    extension.revoked_by_staff_user_id = actor_staff_user_id
    extension.revocation_reason = reason
    db_session.commit()
    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="EMERGENCY_EXTENSION_REVOKED",
        entity_type="emergency_extension",
        entity_public_id=str(extension.id),
        after_state={"status": "REVOKED"},
        reason=reason,
    )


def is_emergency_extension_active(subscription_id, *, now: datetime | None = None) -> bool:
    """Pure lookup used by resolve_commercial_state() callers to decide
    whether to pass an override. Never called from inside
    resolve_commercial_state() itself -- that function stays a pure,
    I/O-free decision function (see its own module docstring)."""
    now = now or utcnow()
    return _active_extension_for(subscription_id, now) is not None
