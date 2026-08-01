"""Phase 9.5A Milestone 22/6, hardened Phase 9.5C Milestone 13 --
LeadConversionService.

See docs/owner/phase9_5a/lead-conversion-contract.md and
docs/owner/phase9_5c/lead-conversion-implementation.md. Transactional,
idempotent (reuses the shared owner_commercial_operations_idempotency_keys
ledger, the same real check-then-record pattern already proven by
issue_license_key(), owner/app/licensing/services.py), duplicate-aware
(reuses the real, existing find_duplicate_candidates(), no new
duplicate-detection logic).

Deliberately never creates a Subscription, License, Installation, Quote,
Invoice, Payment, or Commission -- confirmed by the fact that this module
imports none of those model/service modules.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select

from app.audit.services import record as audit_record
from app.customers.services import find_duplicate_candidates
from app.extensions import db_session
from app.leads.contacts import contact_fields_for_customer_copy, list_lead_contacts
from app.leads.errors import LeadError
from app.models.base import utcnow
from app.models.commercial_sales import CommercialOperationsIdempotencyKey
from app.models.customers import Customer, CustomerContact
from app.models.employees import EmployeeProfile
from app.models.leads import Lead, LeadStatusHistory

OPERATION_CODE = "LEAD_CONVERSION"


class DuplicateCustomerError(Exception):
    def __init__(self, candidates: list[Customer]):
        self.candidates = candidates
        super().__init__("DUPLICATE_CUSTOMER")


class InvalidLeadStateError(Exception):
    pass


def convert(
    lead: Lead,
    *,
    actor_staff_user_id: uuid.UUID,
    existing_customer_id: uuid.UUID | None = None,
    idempotency_key: str,
    expected_version: int | None = None,
) -> Customer:
    existing_key = db_session.execute(
        select(CommercialOperationsIdempotencyKey).where(
            CommercialOperationsIdempotencyKey.idempotency_key == idempotency_key,
            CommercialOperationsIdempotencyKey.operation_code == OPERATION_CODE,
        )
    ).scalars().first()
    if existing_key is not None:
        replayed_customer = db_session.get(Customer, existing_key.result_reference_id)
        # Phase 9.5C -- conflict detection: the same idempotency_key must
        # always resolve to a conversion OF THIS LEAD. Reusing a key across
        # two different Leads (a real client bug, or a replay attack) is
        # rejected rather than silently returning the wrong Customer.
        if replayed_customer is None or replayed_customer.converted_from_lead_id != lead.id:
            raise LeadError("IDEMPOTENCY_CONFLICT")
        return replayed_customer

    if expected_version is not None and lead.version != expected_version:
        raise LeadError("STALE_LEAD_VERSION")

    # Phase 9.5C -- a Lead already CONFIRMED cannot be converted a second
    # time through a *new* idempotency key (the block above already
    # handles the *same* key). LOST/ARCHIVED were already guarded.
    if lead.status in ("LOST", "ARCHIVED", "CONFIRMED"):
        raise InvalidLeadStateError(f"cannot convert a lead in status {lead.status}")

    if existing_customer_id is not None:
        customer = db_session.get(Customer, existing_customer_id)
        customer.lifecycle_status = "ACTIVE"
        customer.converted_from_lead_id = lead.id
        customer.version += 1
    else:
        candidates = find_duplicate_candidates(
            legal_name=lead.organization_or_prospect_name,
            commercial_registration_reference=None,
            email=lead.email,
            phone=lead.phone,
        )
        if candidates:
            raise DuplicateCustomerError(candidates)

        assigned_staff_user_id = None
        if lead.assigned_employee_profile_id:
            assigned_profile = db_session.get(EmployeeProfile, lead.assigned_employee_profile_id)
            assigned_staff_user_id = assigned_profile.staff_user_id if assigned_profile else None

        customer = Customer(
            legal_name=lead.organization_or_prospect_name,
            lifecycle_status="ACTIVE",
            assigned_sales_staff_id=assigned_staff_user_id,
            converted_from_lead_id=lead.id,
        )
        db_session.add(customer)
        db_session.flush()

        # Phase 9.5C Milestone 13 -- carry the Lead's contacts across to
        # real CustomerContact rows (a Customer needs its own ongoing
        # contact records; unlike interactions/follow-ups/notes/locations,
        # which stay meaningfully historical on the retained Lead row,
        # accessible via Customer.converted_from_lead_id, per this module's
        # original design note).
        for lead_contact in list_lead_contacts(lead.id):
            db_session.add(CustomerContact(customer_id=customer.id, **contact_fields_for_customer_copy(lead_contact)))

    from_status = lead.status
    lead.status = "CONFIRMED"
    lead.converted_at = utcnow()
    lead.version += 1

    # Phase 9.5C -- conversion is a real status transition; it must append
    # to LeadStatusHistory exactly like every other transition does
    # (change_lead_status() already does this -- convert() previously did
    # not, an inconsistency this milestone closes).
    db_session.add(
        LeadStatusHistory(
            lead_id=lead.id,
            from_status=from_status,
            to_status="CONFIRMED",
            changed_by_employee_profile_id=lead.assigned_employee_profile_id or lead.created_by_employee_profile_id,
            changed_at=utcnow(),
        )
    )

    db_session.add(
        CommercialOperationsIdempotencyKey(
            idempotency_key=idempotency_key,
            operation_code=OPERATION_CODE,
            result_reference_id=customer.id,
        )
    )
    db_session.commit()

    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="LEAD_CONVERTED",
        entity_type="lead",
        entity_public_id=str(lead.id),
        before_state={"status": from_status},
        after_state={"status": "CONFIRMED", "customer_id": str(customer.id)},
    )
    return customer
