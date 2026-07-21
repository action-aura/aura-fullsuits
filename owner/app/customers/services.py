"""Customer organization services (Part K)."""
from __future__ import annotations

from sqlalchemy import func, or_, select

from app.audit.services import record as audit_record
from app.extensions import db_session
from app.models.base import utcnow
from app.models.customers import Customer, CustomerContact, CustomerNote


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
    return candidates


def create_customer(fields: dict, actor_staff_user_id) -> Customer:
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
