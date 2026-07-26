"""Pilot lifecycle (Phase 8 Part M, Milestone 4).

Conversion to paid is deliberately NOT a standalone action in this module
-- see `PilotRecord`'s own docstring (app/models/commercial_ops.py) and
`mark_pilot_converted()` below. A pilot converts by going through the real,
unmodified Milestone 2 renewal pipeline
(commercial_ops/renewal_requests.py): create a renewal request against the
pilot's subscription, get it approved (separation-of-duties enforced
there), get it applied (payment/recent-auth enforced there, subscription
flips PILOT -> ACTIVE inside that same transaction). Only once that has
genuinely happened does `mark_pilot_converted()` update this table's own
bookkeeping.
"""
from __future__ import annotations

from datetime import date

from app.audit.services import record as audit_record
from app.extensions import db_session
from app.models.commercial_ops import PilotExtension, PilotRecord, PilotStatusHistory, RenewalRequest
from app.models.subscriptions import Subscription
from app.subscriptions.services import transition_subscription

VALID_TRANSITIONS: dict[str, set[str]] = {
    "DRAFT": {"APPROVED", "CANCELLED"},
    "APPROVED": {"ACTIVE", "CANCELLED"},
    "ACTIVE": {"EXTENDED", "CONVERTED", "COMPLETED", "CANCELLED"},
    "EXTENDED": {"EXTENDED", "CONVERTED", "COMPLETED", "CANCELLED"},
    "CONVERTED": set(),
    "COMPLETED": set(),
    "CANCELLED": set(),
}


class InvalidPilotTransitionError(ValueError):
    pass


class PilotLifecycleError(ValueError):
    pass


def create_pilot_record(
    *,
    subscription: Subscription,
    pilot_start: date,
    pilot_end: date,
    actor_staff_user_id,
    allowed_device_count: int = 1,
    allowed_workflows: list[str] | None = None,
    support_level: str | None = None,
    sales_owner_staff_user_id=None,
    support_owner_staff_user_id=None,
    agreed_limitations: str | None = None,
    success_criteria: str | None = None,
    review_date: date | None = None,
    exit_rollback_plan: str | None = None,
    max_extensions_allowed: int = 2,
    platform_id=None,
) -> PilotRecord:
    if subscription.status != "PILOT":
        raise PilotLifecycleError(
            f"Subscription must already be in PILOT status to create a pilot record (was {subscription.status})."
        )
    if pilot_end <= pilot_start:
        raise PilotLifecycleError("pilot_end must be after pilot_start.")

    pilot = PilotRecord(
        subscription_id=subscription.id,
        customer_id=subscription.customer_id,
        product_id=subscription.product_id,
        platform_id=platform_id,
        pilot_start=pilot_start,
        pilot_end=pilot_end,
        allowed_device_count=allowed_device_count,
        allowed_workflows=allowed_workflows,
        support_level=support_level,
        sales_owner_staff_user_id=sales_owner_staff_user_id,
        support_owner_staff_user_id=support_owner_staff_user_id,
        agreed_limitations=agreed_limitations,
        success_criteria=success_criteria,
        review_date=review_date,
        exit_rollback_plan=exit_rollback_plan,
        max_extensions_allowed=max_extensions_allowed,
        created_by_staff_user_id=actor_staff_user_id,
        status="DRAFT",
    )
    db_session.add(pilot)
    db_session.commit()
    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="PILOT_CREATED",
        entity_type="pilot_record",
        entity_public_id=str(pilot.id),
        after_state={"status": "DRAFT", "subscription_id": str(subscription.id)},
    )
    return pilot


def transition_pilot(pilot: PilotRecord, to_status: str, actor_staff_user_id, reason: str | None = None) -> None:
    allowed = VALID_TRANSITIONS.get(pilot.status, set())
    if to_status not in allowed:
        raise InvalidPilotTransitionError(f"Cannot transition pilot from {pilot.status} to {to_status}.")
    from_status = pilot.status
    pilot.status = to_status
    db_session.add(
        PilotStatusHistory(
            pilot_record_id=pilot.id, from_status=from_status, to_status=to_status,
            changed_by_staff_user_id=actor_staff_user_id, reason=reason,
        )
    )
    db_session.commit()
    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="PILOT_STATUS_CHANGED",
        entity_type="pilot_record",
        entity_public_id=str(pilot.id),
        before_state={"status": from_status},
        after_state={"status": to_status},
        reason=reason,
    )


def approve_pilot(pilot: PilotRecord, actor_staff_user_id) -> None:
    transition_pilot(pilot, "APPROVED", actor_staff_user_id)
    pilot.approved_by_staff_user_id = actor_staff_user_id
    db_session.commit()


def activate_pilot(pilot: PilotRecord, actor_staff_user_id) -> None:
    transition_pilot(pilot, "ACTIVE", actor_staff_user_id)


def extend_pilot(pilot: PilotRecord, *, new_end_date: date, reason: str, actor_staff_user_id) -> PilotExtension:
    """Part M: "extension length limited... reason required... extension
    count tracked... no indefinite rolling pilot." All four enforced here,
    not left as convention."""
    if pilot.status not in ("ACTIVE", "EXTENDED"):
        raise InvalidPilotTransitionError(f"Cannot extend a pilot in status {pilot.status}.")
    if not reason or not reason.strip():
        raise PilotLifecycleError("A reason is required to extend a pilot.")
    if pilot.extension_count >= pilot.max_extensions_allowed:
        raise PilotLifecycleError(
            f"Pilot has already been extended {pilot.extension_count} time(s) "
            f"(max_extensions_allowed={pilot.max_extensions_allowed}). No indefinite rolling pilot."
        )
    if new_end_date <= pilot.pilot_end:
        raise PilotLifecycleError("new_end_date must be after the current pilot_end.")

    previous_end = pilot.pilot_end
    from_status = pilot.status
    pilot.pilot_end = new_end_date
    pilot.extension_count += 1
    pilot.status = "EXTENDED"

    extension = PilotExtension(
        pilot_record_id=pilot.id,
        extension_number=pilot.extension_count,
        previous_end_date=previous_end,
        new_end_date=new_end_date,
        reason=reason,
        approved_by_staff_user_id=actor_staff_user_id,
    )
    db_session.add(extension)
    db_session.add(
        PilotStatusHistory(
            pilot_record_id=pilot.id, from_status=from_status, to_status="EXTENDED",
            changed_by_staff_user_id=actor_staff_user_id, reason=reason,
        )
    )
    db_session.commit()
    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="PILOT_EXTENDED",
        entity_type="pilot_record",
        entity_public_id=str(pilot.id),
        before_state={"pilot_end": str(previous_end)},
        after_state={"pilot_end": str(new_end_date), "extension_count": pilot.extension_count},
        reason=reason,
    )
    return extension


def mark_pilot_converted(pilot: PilotRecord, applied_renewal_request: RenewalRequest, actor_staff_user_id) -> None:
    """Call ONLY after apply_renewal_request() has genuinely applied a
    renewal moving the underlying subscription off PILOT status. Never
    grants paid status itself -- purely records that it already happened,
    through the full separation-of-duties/payment/recent-auth-gated
    pipeline (Part M: "no silent conversion")."""
    if applied_renewal_request.status != "APPLIED":
        raise PilotLifecycleError("Renewal request must be APPLIED before a pilot can be marked converted.")
    if applied_renewal_request.subscription_id != pilot.subscription_id:
        raise PilotLifecycleError("Renewal request does not belong to this pilot's subscription.")
    if pilot.status not in ("ACTIVE", "EXTENDED"):
        raise InvalidPilotTransitionError(f"Cannot convert a pilot in status {pilot.status}.")

    from_status = pilot.status
    pilot.status = "CONVERTED"
    pilot.conversion_decision = "CONVERT"
    pilot.converted_renewal_request_id = applied_renewal_request.id
    db_session.add(
        PilotStatusHistory(
            pilot_record_id=pilot.id, from_status=from_status, to_status="CONVERTED",
            changed_by_staff_user_id=actor_staff_user_id,
            reason=f"Converted via renewal request {applied_renewal_request.id}.",
        )
    )
    db_session.commit()
    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="PILOT_CONVERTED",
        entity_type="pilot_record",
        entity_public_id=str(pilot.id),
        after_state={"status": "CONVERTED", "renewal_request_id": str(applied_renewal_request.id)},
    )


def complete_pilot(pilot: PilotRecord, *, actor_staff_user_id, reason: str | None = None) -> None:
    """For a pilot that ends WITHOUT conversion (Part M's "no conversion"
    outcome). Also transitions the underlying subscription PILOT ->
    COMPLETED via the existing, unmodified transition_subscription() --
    already permitted in the shared table before Phase 8, not widened
    here."""
    transition_pilot(pilot, "COMPLETED", actor_staff_user_id, reason=reason)
    pilot.conversion_decision = "DO_NOT_CONVERT"
    db_session.commit()
    subscription = pilot.subscription
    if subscription.status == "PILOT":
        transition_subscription(subscription, "COMPLETED", actor_staff_user_id, reason=reason)


def cancel_pilot(pilot: PilotRecord, *, reason: str, actor_staff_user_id) -> None:
    if not reason or not reason.strip():
        raise PilotLifecycleError("A reason is required to cancel a pilot.")
    transition_pilot(pilot, "CANCELLED", actor_staff_user_id, reason=reason)
    subscription = pilot.subscription
    if subscription.status == "PILOT":
        transition_subscription(subscription, "CANCELLED", actor_staff_user_id, reason=reason)
