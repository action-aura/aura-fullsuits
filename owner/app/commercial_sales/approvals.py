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

import hashlib
import json
import uuid
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select

from app.audit.services import record as audit_record
from app.commercial_sales.errors import APPROVAL_TRANSITIONS, CommercialSalesError
from app.extensions import db_session
from app.models.base import utcnow
from app.models.commercial_sales import CommercialApproval, Quote, QuoteLine


_FINGERPRINT_CENT = Decimal("0.01")


def _quantize_for_fingerprint(value: Decimal) -> str:
    """Real bug found and fixed while building this: SQLAlchemy expires
    ORM instances by default after commit() -- the next attribute read
    re-fetches from the DB and re-coerces the value through the actual
    NUMERIC(12,2) column type. Decimal("0") and Decimal("0.00") are
    numerically equal but str() differently ("0" vs "0.00"), so a
    fingerprint computed pre-commit (from a caller-supplied Decimal of
    unknown precision) would never match one recomputed post-commit
    (always exactly 2 decimal places) even with zero material change.
    Quantizing to the column's own precision before hashing makes both
    sides always agree."""
    return str(value.quantize(_FINGERPRINT_CENT, rounding=ROUND_HALF_UP))


def compute_line_commercial_fingerprint(
    *,
    plan_id: uuid.UUID | None,
    addon_id: uuid.UUID | None,
    quantity: int,
    unit_price: Decimal,
    overridden_unit_price: Decimal | None,
    discount_amount: Decimal | None,
    currency: str,
) -> str:
    """Deterministic content fingerprint of the material commercial values
    an approval decision is actually about -- product/plan, quantity,
    list price, proposed (override) price, discount, currency. Stronger
    proof than a version counter: it can never miss a material change
    regardless of which future code path made it (a version counter is
    only trustworthy if every relevant mutation reliably bumps it), and
    is provably indifferent to unrelated actions (viewing, audit writes,
    sibling-line/document-level changes that don't touch this line) since
    those never appear in the payload at all."""
    payload = {
        "plan_id": str(plan_id) if plan_id else None,
        "addon_id": str(addon_id) if addon_id else None,
        "quantity": quantity,
        "unit_price": _quantize_for_fingerprint(unit_price),
        "overridden_unit_price": _quantize_for_fingerprint(overridden_unit_price) if overridden_unit_price is not None else None,
        "discount_amount": _quantize_for_fingerprint(discount_amount) if discount_amount is not None else _quantize_for_fingerprint(Decimal("0")),
        "currency": currency,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _current_fingerprint_for_target(target_type: str, target_id: uuid.UUID) -> str | None:
    """Recomputes the fingerprint from the target's CURRENT persisted
    state. Returns None if the target no longer exists (a deleted line --
    treated as stale, never as 'nothing to check')."""
    if target_type == "QUOTE_LINE":
        line = db_session.get(QuoteLine, target_id)
        if line is None:
            return None
        quote = db_session.get(Quote, line.quote_id)
        return compute_line_commercial_fingerprint(
            plan_id=line.plan_id,
            addon_id=line.addon_id,
            quantity=line.quantity,
            unit_price=line.unit_price,
            overridden_unit_price=line.overridden_unit_price,
            discount_amount=line.discount_amount,
            currency=quote.currency,
        )
    raise NotImplementedError(f"_current_fingerprint_for_target has no rule for target_type={target_type!r}")


def create_approval_request(
    *,
    target_type: str,
    target_id: uuid.UUID,
    target_version_at_request: int,
    commercial_fingerprint: str,
    reason_code: str,
    requested_values: dict,
    original_values: dict,
    requested_by_staff_user_id: uuid.UUID,
) -> CommercialApproval:
    approval = CommercialApproval(
        target_type=target_type,
        target_id=target_id,
        target_version_at_request=target_version_at_request,
        commercial_fingerprint=commercial_fingerprint,
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
) -> CommercialApproval:
    """Non-Negotiable Principle 12: sales staff cannot self-approve
    exceptions. Checked here, in the service layer, regardless of whether
    the caller's permission grant alone would have allowed it -- UI
    hiding is not authorization.

    Staleness is decided by recomputing the target's CURRENT commercial
    fingerprint and comparing to the one captured at request time --
    never by a caller-supplied version number (removed from this
    signature entirely: a caller could otherwise pass a stale or wrong
    value by mistake). See compute_line_commercial_fingerprint()."""
    if approval.requested_by_staff_user_id == decided_by_staff_user_id:
        raise CommercialSalesError("SELF_APPROVAL_FORBIDDEN")

    target = "APPROVED" if approved else "REJECTED"
    _check_transition(approval.status, target)

    if not approved and (not decision_reason or not decision_reason.strip()):
        raise CommercialSalesError("REASON_REQUIRED")

    current_fingerprint = _current_fingerprint_for_target(approval.target_type, approval.target_id)
    if current_fingerprint != approval.commercial_fingerprint:
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
