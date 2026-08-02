"""Phase 9.5D Milestone 6 -- CommercialApproval service.

A separate, per-document approval record (matching the existing
RenewalRequest/PendingActivation/PilotRecord convention: status enum +
dedicated approve/reject function + permanent record) -- deliberately
NOT folded into Quote.status (Milestone 1's audit: a generic Approval
model would itself be the duplication-risk anti-pattern this phase's
audit exists to prevent). See
docs/owner/phase9_5d/commercial-approval-contract.md,
discount-and-price-override-policy.md.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select

from app.audit.services import record as audit_record
from app.commercial_sales.errors import APPROVAL_TRANSITIONS, CommercialSalesError
from app.extensions import db_session
from app.models.base import utcnow
from app.models.commercial_sales import CommercialApproval


def create_approval_request(
    *,
    target_type: str,
    target_id: uuid.UUID,
    target_version_at_request: int,
    reason_code: str,
    requested_values: dict,
    original_values: dict,
    requested_by_staff_user_id: uuid.UUID,
) -> CommercialApproval:
    approval = CommercialApproval(
        target_type=target_type,
        target_id=target_id,
        target_version_at_request=target_version_at_request,
        reason_code=reason_code,
        requested_by_staff_user_id=requested_by_staff_user_id,
        requested_values=requested_values,
        original_values=original_values,
        status="PENDING",
        requested_at=utcnow(),
        version=1,
    )
    db_session.add(approval)
    db_session.flush()
    db_session.commit()

    audit_record(
        actor_staff_user_id=requested_by_staff_user_id,
        actor_role_snapshot=None,
        action_code="COMMERCIAL_APPROVAL_REQUESTED",
        entity_type="commercial_approval",
        entity_public_id=str(approval.id),
        after_state={"target_type": target_type, "target_id": str(target_id), "reason_code": reason_code},
    )
    return approval


def _check_transition(status: str, target: str) -> None:
    if target not in APPROVAL_TRANSITIONS.get(status, set()):
        raise CommercialSalesError("INVALID_APPROVAL_TRANSITION", from_status=status, to_status=target)


def decide_approval(
    approval: CommercialApproval,
    *,
    approved: bool,
    decision_reason: str | None,
    decided_by_staff_user_id: uuid.UUID,
    current_target_version: int,
) -> CommercialApproval:
    """Non-Negotiable Principle 12: sales staff cannot self-approve
    exceptions. Checked here, in the service layer, regardless of whether
    the caller's permission grant alone would have allowed it -- UI
    hiding is not authorization."""
    if approval.requested_by_staff_user_id == decided_by_staff_user_id:
        raise CommercialSalesError("SELF_APPROVAL_FORBIDDEN")

    target = "APPROVED" if approved else "REJECTED"
    _check_transition(approval.status, target)

    if not approved and (not decision_reason or not decision_reason.strip()):
        raise CommercialSalesError("REASON_REQUIRED")

    if approval.target_version_at_request != current_target_version:
        raise CommercialSalesError("APPROVAL_STALE")

    approval.status = target
    approval.decided_by_staff_user_id = decided_by_staff_user_id
    approval.decision_reason = decision_reason
    approval.decided_at = utcnow()
    approval.version += 1
    db_session.commit()

    audit_record(
        actor_staff_user_id=decided_by_staff_user_id,
        actor_role_snapshot=None,
        action_code="COMMERCIAL_APPROVAL_APPROVED" if approved else "COMMERCIAL_APPROVAL_REJECTED",
        entity_type="commercial_approval",
        entity_public_id=str(approval.id),
        reason=decision_reason,
        after_state={"status": target},
    )
    return approval


def cancel_approval_request(approval: CommercialApproval, *, actor_staff_user_id: uuid.UUID) -> CommercialApproval:
    _check_transition(approval.status, "CANCELLED")
    approval.status = "CANCELLED"
    approval.decided_at = utcnow()
    approval.version += 1
    db_session.commit()

    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="COMMERCIAL_APPROVAL_CANCELLED",
        entity_type="commercial_approval",
        entity_public_id=str(approval.id),
        after_state={"status": "CANCELLED"},
    )
    return approval


def unresolved_approvals_for_targets(target_type: str, target_ids: list[uuid.UUID]) -> list[CommercialApproval]:
    """Returns every PENDING or REJECTED approval for the given targets --
    used by Milestone 7's acceptance gate to check nothing is blocking.
    APPROVED/CANCELLED/EXPIRED are excluded (resolved, not blocking)."""
    if not target_ids:
        return []
    return db_session.execute(
        select(CommercialApproval).where(
            CommercialApproval.target_type == target_type,
            CommercialApproval.target_id.in_(target_ids),
            CommercialApproval.status.in_(("PENDING", "REJECTED")),
        )
    ).scalars().all()
