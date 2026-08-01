"""Phase 9.5A Milestone 22/6 -- LeadConversionService.

See docs/owner/phase9_5a/lead-conversion-contract.md. Transactional,
idempotent (reuses the shared owner_commercial_operations_idempotency_keys
ledger, the same real check-then-record pattern already proven by
issue_license_key(), owner/app/licensing/services.py), duplicate-aware
(reuses the real, existing find_duplicate_candidates(), no new
duplicate-detection logic).
"""
from __future__ import annotations

import uuid

from sqlalchemy import select

from app.audit.services import record as audit_record
from app.customers.services import find_duplicate_candidates
from app.extensions import db_session
from app.models.base import utcnow
from app.models.commercial_sales import CommercialOperationsIdempotencyKey
from app.models.customers import Customer
from app.models.employees import EmployeeProfile
from app.models.leads import Lead

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
) -> Customer:
    existing_key = db_session.execute(
        select(CommercialOperationsIdempotencyKey).where(
            CommercialOperationsIdempotencyKey.idempotency_key == idempotency_key,
            CommercialOperationsIdempotencyKey.operation_code == OPERATION_CODE,
        )
    ).scalars().first()
    if existing_key is not None:
        return db_session.get(Customer, existing_key.result_reference_id)

    if lead.status in ("LOST", "ARCHIVED"):
        raise InvalidLeadStateError(f"cannot convert a lead in status {lead.status}")

    if existing_customer_id is not None:
        customer = db_session.get(Customer, existing_customer_id)
        customer.lifecycle_status = "ACTIVE"
        customer.converted_from_lead_id = lead.id
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

    lead.status = "CONFIRMED"
    lead.converted_at = utcnow()
    lead.version += 1

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
        before_state={"status": "NOT_CONFIRMED"},
        after_state={"status": "CONFIRMED", "customer_id": str(customer.id)},
    )
    return customer
