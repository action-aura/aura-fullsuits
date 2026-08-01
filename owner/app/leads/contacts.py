"""Phase 9.5C Milestone 8 -- Lead contacts.

Reuses the exact same shape/primary-flag rule as
app.customers.services.add_contact() (Customer's existing, authoritative
contact system) -- not a competing authority, a new table for a genuinely
new concept (a Lead has no existing contact concept at all, per
crm-domain-reuse-matrix.md).
"""
from __future__ import annotations

import uuid

from sqlalchemy import select

from app.audit.services import record as audit_record
from app.extensions import db_session
from app.leads.errors import LeadError
from app.models.leads import LeadContact

_MAX_NAME_LEN = 255
_MAX_TITLE_LEN = 128
_MAX_EMAIL_LEN = 255
_MAX_PHONE_LEN = 64


def add_lead_contact(lead_id: uuid.UUID, fields: dict, actor_staff_user_id: uuid.UUID) -> LeadContact:
    name = (fields.get("name") or "").strip()
    if not name:
        raise LeadError("CONTACT_NAME_REQUIRED")
    if len(name) > _MAX_NAME_LEN:
        raise LeadError("CONTACT_NAME_TOO_LONG", max_len=_MAX_NAME_LEN)

    is_primary = bool(fields.get("is_primary"))
    if is_primary:
        # One clearly defined primary-contact policy: setting a new primary
        # demotes any existing one for the same Lead, in the same
        # transaction -- never two primaries, never a race between two
        # concurrent "set primary" calls landing as two primaries.
        db_session.execute(
            LeadContact.__table__.update()
            .where(LeadContact.lead_id == lead_id, LeadContact.archived_at.is_(None))
            .values(is_primary=False)
        )

    contact = LeadContact(
        lead_id=lead_id,
        name=name,
        title=(fields.get("title") or None),
        business_email=(fields.get("business_email") or None),
        business_phone=(fields.get("business_phone") or None),
        preferred_channel=(fields.get("preferred_channel") or None),
        is_primary=is_primary,
        notes=(fields.get("notes") or None),
    )
    db_session.add(contact)
    db_session.commit()
    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="LEAD_CONTACT_ADDED",
        entity_type="lead",
        entity_public_id=str(lead_id),
        after_state={"contact_name": contact.name, "is_primary": contact.is_primary},
    )
    return contact


def update_lead_contact(contact: LeadContact, fields: dict, actor_staff_user_id: uuid.UUID, *, expected_version: int | None = None) -> LeadContact:
    if expected_version is not None and contact.version != expected_version:
        raise LeadError("STALE_LEAD_VERSION")
    if fields.get("is_primary"):
        db_session.execute(
            LeadContact.__table__.update()
            .where(LeadContact.lead_id == contact.lead_id, LeadContact.id != contact.id, LeadContact.archived_at.is_(None))
            .values(is_primary=False)
        )
    for key in ("name", "title", "business_email", "business_phone", "preferred_channel", "notes", "is_primary"):
        if key in fields:
            setattr(contact, key, fields[key])
    contact.version += 1
    db_session.commit()
    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="LEAD_CONTACT_UPDATED",
        entity_type="lead",
        entity_public_id=str(contact.lead_id),
    )
    return contact


def list_lead_contacts(lead_id: uuid.UUID):
    stmt = select(LeadContact).where(LeadContact.lead_id == lead_id, LeadContact.archived_at.is_(None)).order_by(LeadContact.is_primary.desc(), LeadContact.created_at)
    return db_session.execute(stmt).scalars().all()


def contact_fields_for_customer_copy(lead_contact: LeadContact) -> dict:
    """The exact 1:1 field mapping conversion.py (Milestone 13) uses to
    carry a Lead's contacts across to CustomerContact rows -- one place,
    reused, instead of the mapping being re-derived at the call site."""
    return {
        "name": lead_contact.name,
        "title": lead_contact.title,
        "business_email": lead_contact.business_email,
        "business_phone": lead_contact.business_phone,
        "preferred_channel": lead_contact.preferred_channel,
        "is_primary": lead_contact.is_primary,
        "notes": lead_contact.notes,
    }
