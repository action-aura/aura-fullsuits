"""Customer organization services (Part K)."""
from __future__ import annotations

import re
import uuid

from sqlalchemy import func, or_, select

from app.audit.services import record as audit_record
from app.extensions import db_session
from app.leads.errors import CustomerCrmError
from app.models.base import utcnow
from app.models.customers import Customer, CustomerContact, CustomerNote
from app.models.leads import CustomerAssignment
from app.models.staff import StaffUser


def normalize_phone(phone: str) -> str:
    """Digits-only comparison key -- strips spaces/dashes/parens/plus so
    '+962 79 123 4567' and '0791234567'-style variants can still match
    without claiming to be a real E.164 normalizer (Phase 9.5C scope: bounded,
    explainable, deterministic matching, not a phone-parsing library)."""
    return re.sub(r"\D", "", phone or "")


def find_duplicate_candidates(legal_name: str, commercial_registration_reference: str | None, email: str | None, phone: str | None):
    """Non-blocking duplicate-detection: returns existing customers that look
    like the same organization, for the caller to warn about (Part K).
    Never auto-merges or auto-rejects -- staff makes the final call."""
    clauses = [func.lower(Customer.legal_name) == legal_name.strip().lower()]
    if commercial_registration_reference:
        clauses.append(Customer.commercial_registration_reference == commercial_registration_reference)
    stmt = select(Customer).where(or_(*clauses), Customer.archived_at.is_(None))
    candidates = list(db_session.execute(stmt).scalars().all())
    if email:
        email_stmt = select(Customer).join(CustomerContact).where(
            func.lower(CustomerContact.business_email) == email.strip().lower()
        )
        candidates += [c for c in db_session.execute(email_stmt).scalars().all() if c not in candidates]
    normalized_phone = normalize_phone(phone) if phone else ""
    if normalized_phone:
        # Bounded, explainable: compare against every non-archived contact's
        # normalized phone in Python rather than a DB-side regex/replace
        # chain -- the candidate set is already small (organizations, not
        # millions of rows), and this keeps the matching rule readable and
        # testable in one place instead of split across SQL and Python.
        phone_stmt = select(CustomerContact).join(Customer).where(
            Customer.archived_at.is_(None), CustomerContact.business_phone.isnot(None)
        )
        for contact in db_session.execute(phone_stmt).scalars().all():
            if normalize_phone(contact.business_phone) == normalized_phone and contact.customer not in candidates:
                candidates.append(contact.customer)
    return candidates


def assign_customer(
    customer: Customer,
    assigned_to_staff_user_id: uuid.UUID,
    actor_staff_user_id: uuid.UUID,
    *,
    reason: str | None = None,
    expected_version: int | None = None,
) -> CustomerAssignment:
    """Mirrors app.leads.services.assign_lead's exact append-only close/open
    pattern -- see docs/owner/phase9_5c/assignment-and-reassignment-contract.md."""
    if expected_version is not None and customer.version != expected_version:
        raise CustomerCrmError("STALE_CUSTOMER_VERSION")

    destination = db_session.get(StaffUser, assigned_to_staff_user_id)
    if destination is None or not destination.is_active or destination.disabled_at is not None:
        raise CustomerCrmError("DESTINATION_EMPLOYEE_NOT_ACTIVE")

    now = utcnow()
    current = db_session.execute(
        select(CustomerAssignment).where(
            CustomerAssignment.customer_id == customer.id, CustomerAssignment.unassigned_at.is_(None)
        )
    ).scalars().first()
    before_assignee = current.assigned_to_staff_user_id if current else None
    if current is not None and (reason or "").strip() == "":
        raise CustomerCrmError("REASON_REQUIRED_FOR_REASSIGN")
    if current is not None:
        current.unassigned_at = now

    new_assignment = CustomerAssignment(
        customer_id=customer.id,
        assigned_to_staff_user_id=assigned_to_staff_user_id,
        assigned_by_staff_user_id=actor_staff_user_id,
        assigned_at=now,
        reason=reason,
    )
    db_session.add(new_assignment)
    customer.assigned_sales_staff_id = assigned_to_staff_user_id
    customer.version += 1
    db_session.commit()
    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="CUSTOMER_REASSIGNED" if before_assignee else "CUSTOMER_ASSIGNED",
        entity_type="customer",
        entity_public_id=str(customer.id),
        reason=reason,
        before_state={"assigned_to_staff_user_id": str(before_assignee) if before_assignee else None},
        after_state={"assigned_to_staff_user_id": str(assigned_to_staff_user_id)},
    )
    return new_assignment


def create_customer(fields: dict, actor_staff_user_id) -> Customer:
    # Phase 9.5C -- a customer with no assignee would be invisible to
    # everyone except a customers.view_all holder once ownership filtering
    # is enforced (Milestone 3); defaulting to the creating actor matches
    # real intent (management may reassign afterward) and preserves every
    # pre-existing "I created it, I can see it" test/behavior.
    fields = {**fields}
    fields.setdefault("assigned_sales_staff_id", actor_staff_user_id)
    customer = Customer(**fields)
    db_session.add(customer)
    db_session.commit()
    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="CUSTOMER_CREATED",
        entity_type="customer",
        entity_public_id=str(customer.id),
        after_state={"legal_name": customer.legal_name, "lifecycle_status": customer.lifecycle_status},
    )
    return customer


def update_customer(customer: Customer, fields: dict, actor_staff_user_id) -> Customer:
    before = {k: getattr(customer, k) for k in fields}
    for k, v in fields.items():
        setattr(customer, k, v)
    db_session.commit()
    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="CUSTOMER_UPDATED",
        entity_type="customer",
        entity_public_id=str(customer.id),
        before_state=before,
        after_state=fields,
    )
    return customer


def archive_customer(customer: Customer, actor_staff_user_id) -> None:
    """Soft-archive only -- active customer records are never hard-deleted (Part K)."""
    customer.lifecycle_status = "ARCHIVED"
    customer.archived_at = utcnow()
    db_session.commit()
    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="CUSTOMER_ARCHIVED",
        entity_type="customer",
        entity_public_id=str(customer.id),
    )


def add_contact(customer: Customer, fields: dict, actor_staff_user_id) -> CustomerContact:
    contact = CustomerContact(customer_id=customer.id, **fields)
    db_session.add(contact)
    db_session.commit()
    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="CUSTOMER_CONTACT_ADDED",
        entity_type="customer",
        entity_public_id=str(customer.id),
        after_state={"contact_name": contact.name},
    )
    return contact


def add_note(customer: Customer, body: str, actor_staff_user_id) -> CustomerNote:
    note = CustomerNote(customer_id=customer.id, author_staff_user_id=actor_staff_user_id, body=body)
    db_session.add(note)
    db_session.commit()
    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="CUSTOMER_NOTE_ADDED",
        entity_type="customer",
        entity_public_id=str(customer.id),
    )
    return note
