"""Phase 9.5A Milestone 22 -- LeadService / LeadAssignmentService /
CustomerLocationService.

Foundation operations only: create lead, list own (ownership-filtered +
paginated), management list all, assign/reassign (append-only), change
status (append-only history), add a lead/customer note, capture a location.
Follows owner/app/customers/services.py's exact pattern.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select

from app.audit.services import record as audit_record
from app.extensions import db_session
from app.leads.errors import LeadError, validate_lead_transition
from app.leads.ownership import apply_ownership_filter
from app.models.base import utcnow
from app.models.employees import EmployeeProfile
from app.models.leads import CustomerLocation, Lead, LeadAssignment, LeadNote, LeadStatusHistory
from app.services.pagination import DEFAULT_PAGE_SIZE, paginate


def create_lead(fields: dict, actor_employee_profile_id: uuid.UUID, actor_staff_user_id: uuid.UUID) -> Lead:
    lead = Lead(**fields, created_by_employee_profile_id=actor_employee_profile_id, status="NEW")
    db_session.add(lead)
    db_session.flush()

    db_session.add(
        LeadStatusHistory(
            lead_id=lead.id,
            from_status=None,
            to_status="NEW",
            changed_by_employee_profile_id=actor_employee_profile_id,
            changed_at=utcnow(),
        )
    )
    if lead.assigned_employee_profile_id:
        db_session.add(
            LeadAssignment(
                lead_id=lead.id,
                assigned_to_employee_profile_id=lead.assigned_employee_profile_id,
                assigned_by_employee_profile_id=actor_employee_profile_id,
                assigned_at=utcnow(),
            )
        )
    db_session.commit()
    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="LEAD_CREATED",
        entity_type="lead",
        entity_public_id=str(lead.id),
        after_state={"organization_or_prospect_name": lead.organization_or_prospect_name, "status": lead.status},
    )
    return lead


def list_own_leads(actor_employee_profile_id: uuid.UUID, *, page: int = 1, page_size: int = DEFAULT_PAGE_SIZE) -> dict:
    stmt = apply_ownership_filter(
        select(Lead).order_by(Lead.created_at.desc()), Lead, actor_employee_profile_id, all_permission_held=False
    )
    return paginate(stmt, page, page_size)


def list_all_leads(*, page: int = 1, page_size: int = DEFAULT_PAGE_SIZE) -> dict:
    stmt = select(Lead).order_by(Lead.created_at.desc())
    return paginate(stmt, page, page_size)


def change_lead_status(
    lead: Lead,
    to_status: str,
    actor_employee_profile_id: uuid.UUID,
    actor_staff_user_id: uuid.UUID,
    reason: str | None = None,
    *,
    expected_version: int | None = None,
) -> Lead:
    if expected_version is not None and lead.version != expected_version:
        raise LeadError("STALE_LEAD_VERSION")
    from_status = lead.status
    validate_lead_transition(from_status, to_status, reason)
    lead.status = to_status
    lead.version += 1
    if to_status == "LOST":
        lead.lost_at = utcnow()
    db_session.add(
        LeadStatusHistory(
            lead_id=lead.id,
            from_status=from_status,
            to_status=to_status,
            changed_by_employee_profile_id=actor_employee_profile_id,
            reason=reason,
            changed_at=utcnow(),
        )
    )
    db_session.commit()
    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="LEAD_STATUS_CHANGED",
        entity_type="lead",
        entity_public_id=str(lead.id),
        reason=reason,
        before_state={"status": from_status},
        after_state={"status": to_status},
    )
    return lead


def assign_lead(
    lead: Lead,
    assigned_to_employee_profile_id: uuid.UUID,
    actor_employee_profile_id: uuid.UUID,
    actor_staff_user_id: uuid.UUID,
    *,
    reason: str | None = None,
    expected_version: int | None = None,
) -> LeadAssignment:
    """Reassignment closes the currently-open assignment row and opens a new
    one -- never an in-place update (lead-customer-domain-model.md)."""
    if expected_version is not None and lead.version != expected_version:
        raise LeadError("STALE_LEAD_VERSION")

    destination = db_session.get(EmployeeProfile, assigned_to_employee_profile_id)
    if destination is None or destination.employment_status != "ACTIVE":
        raise LeadError("DESTINATION_EMPLOYEE_NOT_ACTIVE")

    now = utcnow()
    current = db_session.execute(
        select(LeadAssignment).where(LeadAssignment.lead_id == lead.id, LeadAssignment.unassigned_at.is_(None))
    ).scalars().first()
    before_assignee = current.assigned_to_employee_profile_id if current else None
    if current is not None and (reason or "").strip() == "":
        raise LeadError("REASON_REQUIRED_FOR_REASSIGN")
    if current is not None:
        current.unassigned_at = now

    new_assignment = LeadAssignment(
        lead_id=lead.id,
        assigned_to_employee_profile_id=assigned_to_employee_profile_id,
        assigned_by_employee_profile_id=actor_employee_profile_id,
        assigned_at=now,
        reason=reason,
    )
    db_session.add(new_assignment)
    lead.assigned_employee_profile_id = assigned_to_employee_profile_id
    lead.version += 1
    db_session.commit()
    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="LEAD_REASSIGNED" if before_assignee else "LEAD_ASSIGNED",
        entity_type="lead",
        entity_public_id=str(lead.id),
        reason=reason,
        before_state={"assigned_to_employee_profile_id": str(before_assignee) if before_assignee else None},
        after_state={"assigned_to_employee_profile_id": str(assigned_to_employee_profile_id)},
    )
    return new_assignment


def add_lead_note(lead: Lead, body: str, actor_employee_profile_id: uuid.UUID, actor_staff_user_id: uuid.UUID) -> LeadNote:
    note = LeadNote(lead_id=lead.id, author_employee_profile_id=actor_employee_profile_id, body=body)
    db_session.add(note)
    db_session.commit()
    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="LEAD_NOTE_ADDED",
        entity_type="lead",
        entity_public_id=str(lead.id),
    )
    return note


def capture_location(
    *,
    lead_id: uuid.UUID | None,
    customer_id: uuid.UUID | None,
    fields: dict,
    actor_employee_profile_id: uuid.UUID,
    actor_staff_user_id: uuid.UUID,
) -> CustomerLocation:
    """Exactly one of lead_id/customer_id must be set (DB CHECK constraint is
    the ultimate guard; this raises early with a clear error instead of
    relying only on the constraint violation)."""
    if (lead_id is None) == (customer_id is None):
        raise ValueError("capture_location requires exactly one of lead_id or customer_id")
    location = CustomerLocation(
        lead_id=lead_id,
        customer_id=customer_id,
        **fields,
        captured_by_employee_profile_id=actor_employee_profile_id,
        captured_at=utcnow(),
    )
    db_session.add(location)
    db_session.commit()
    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="CUSTOMER_LOCATION_CAPTURED",
        entity_type="lead" if lead_id else "customer",
        entity_public_id=str(lead_id or customer_id),
        after_state={"source": location.source, "verified": location.verified},
    )
    return location
