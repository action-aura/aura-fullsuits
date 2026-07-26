"""Activation policy resolution and manual-approval workflow (Phase 8 Part O,
Milestone 5).

See `app/models/activation_governance.py`'s `ActivationPolicy`/
`PendingActivation` docstrings for what these are and why `AUTOMATIC`
(what every existing installation was always activated under) is
indistinguishable from "no policy configured at all."
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import select

from app.audit.services import record as audit_record
from app.extensions import db_session
from app.installations.services import count_slot_consuming_installations, transition_installation
from app.models.activation_governance import ActivationPolicy, PendingActivation
from app.models.base import utcnow
from app.models.installations import Installation
from app.models.licensing import License

DEFAULT_MODE = "AUTOMATIC"


class ActivationPolicyError(ValueError):
    pass


class PendingActivationError(ValueError):
    pass


def resolve_activation_mode(product_id, *, as_of: date | None = None) -> str:
    """Product-specific active policy first, falling back to the global
    default (`product_id IS NULL`), falling back to `AUTOMATIC` if neither
    exists -- deny-by-default does NOT apply here the way it does to
    commercial state (Milestone 1): the safe default for an unconfigured
    product is to preserve the exact pre-Phase-8 activation behavior, not
    to block activation outright."""
    as_of = as_of or utcnow().date()
    stmt = (
        select(ActivationPolicy)
        .where(
            ActivationPolicy.product_id == product_id,
            ActivationPolicy.effective_date <= as_of,
        )
        .where((ActivationPolicy.retired_date.is_(None)) | (ActivationPolicy.retired_date > as_of))
        .order_by(ActivationPolicy.effective_date.desc())
    )
    specific = db_session.execute(stmt).scalars().first()
    if specific is not None:
        return specific.mode

    default_stmt = (
        select(ActivationPolicy)
        .where(
            ActivationPolicy.product_id.is_(None),
            ActivationPolicy.effective_date <= as_of,
        )
        .where((ActivationPolicy.retired_date.is_(None)) | (ActivationPolicy.retired_date > as_of))
        .order_by(ActivationPolicy.effective_date.desc())
    )
    default = db_session.execute(default_stmt).scalars().first()
    return default.mode if default is not None else DEFAULT_MODE


def create_activation_policy(
    *,
    policy_code: str,
    mode: str,
    effective_date: date,
    actor_staff_user_id,
    product_id=None,
    notes: str | None = None,
) -> ActivationPolicy:
    from app.models.activation_governance import ACTIVATION_MODES

    if mode not in ACTIVATION_MODES:
        raise ActivationPolicyError(f"Unknown activation mode: {mode}")
    policy = ActivationPolicy(
        policy_code=policy_code, product_id=product_id, mode=mode,
        effective_date=effective_date, notes=notes, created_by_staff_user_id=actor_staff_user_id,
    )
    db_session.add(policy)
    db_session.commit()
    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="ACTIVATION_POLICY_CREATED",
        entity_type="activation_policy",
        entity_public_id=str(policy.id),
        after_state={"policy_code": policy_code, "mode": mode, "product_id": str(product_id) if product_id else None},
    )
    return policy


def create_pending_activation(
    *,
    installation: Installation,
    license_id,
    product_id,
    platform_id,
    mode: str,
    request_id: str | None = None,
    correlation_id: str | None = None,
    device_key_fingerprint: str | None = None,
) -> PendingActivation:
    """Idempotent: a retry of the same still-gated installation returns the
    existing open row rather than raising or duplicating (mirrors
    create_renewal_request()'s idempotency_key pattern and
    create_notification()'s dedup_key pattern) -- the UNIQUE constraint on
    `installation_id` is the actual guarantee."""
    existing = db_session.execute(
        select(PendingActivation).where(PendingActivation.installation_id == installation.id)
    ).scalars().first()
    if existing is not None:
        return existing

    pending = PendingActivation(
        installation_id=installation.id, license_id=license_id, product_id=product_id, platform_id=platform_id,
        request_id=request_id, correlation_id=correlation_id, device_key_fingerprint=device_key_fingerprint,
        mode=mode, status="PENDING_REVIEW",
    )
    db_session.add(pending)
    db_session.commit()
    audit_record(
        actor_staff_user_id=None,
        actor_role_snapshot=None,
        action_code="ACTIVATION_PENDING_REVIEW_CREATED",
        entity_type="pending_activation",
        entity_public_id=str(pending.id),
        after_state={"installation_id": str(installation.id), "mode": mode},
        correlation_id=correlation_id,
    )
    return pending


def approve_pending_activation(pending: PendingActivation, actor_staff_user_id) -> None:
    if pending.status != "PENDING_REVIEW":
        raise PendingActivationError(f"Cannot approve a pending activation in status {pending.status}.")

    installation = db_session.execute(
        select(Installation).where(Installation.id == pending.installation_id).with_for_update()
    ).scalars().first()
    if installation is None or installation.status != "PENDING_ACTIVATION":
        raise PendingActivationError("Installation is no longer awaiting activation.")

    license_row = db_session.execute(
        select(License).where(License.id == pending.license_id).with_for_update()
    ).scalars().first()
    if license_row is None:
        raise PendingActivationError("LICENSE_NOT_FOUND")

    # Defensive recheck (Part F): the effective device limit may have moved
    # since this request was gated -- another installation activated, or a
    # temporary exception (Part P) expired. Approval is not a rubber stamp.
    from app.commercial_ops.device_slot_ops import resolve_effective_device_limit

    active_count = count_slot_consuming_installations(license_row.id, exclude_installation_id=installation.id)
    if active_count >= resolve_effective_device_limit(license_row):
        raise PendingActivationError("DEVICE_LIMIT_REACHED")

    transition_installation(
        installation, "ACTIVE", actor_staff_user_id, reason="Activation approved after manual review."
    )
    pending.status = "APPROVED"
    pending.decided_by_staff_user_id = actor_staff_user_id
    pending.decided_at = utcnow()
    db_session.commit()
    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="PENDING_ACTIVATION_APPROVED",
        entity_type="pending_activation",
        entity_public_id=str(pending.id),
        after_state={"status": "APPROVED"},
    )


def reject_pending_activation(pending: PendingActivation, actor_staff_user_id, *, reason: str) -> None:
    if pending.status != "PENDING_REVIEW":
        raise PendingActivationError(f"Cannot reject a pending activation in status {pending.status}.")
    if not reason or not reason.strip():
        raise PendingActivationError("A reason is required to reject a pending activation.")

    installation = db_session.execute(
        select(Installation).where(Installation.id == pending.installation_id).with_for_update()
    ).scalars().first()
    if installation is not None and installation.status == "PENDING_ACTIVATION":
        # Frees the reserved slot -- DEACTIVATED is not in
        # SLOT_CONSUMING_STATUSES, so a rejected request never silently
        # occupies a paid device slot forever.
        transition_installation(installation, "DEACTIVATED", actor_staff_user_id, reason=reason)

    pending.status = "REJECTED"
    pending.decided_by_staff_user_id = actor_staff_user_id
    pending.decided_at = utcnow()
    pending.decision_reason = reason
    db_session.commit()
    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="PENDING_ACTIVATION_REJECTED",
        entity_type="pending_activation",
        entity_public_id=str(pending.id),
        after_state={"status": "REJECTED"},
        reason=reason,
    )
