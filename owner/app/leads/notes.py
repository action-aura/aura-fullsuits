"""Phase 9.5C Milestone 11 -- note visibility enforcement.

Both LeadNote and CustomerNote gained a `visibility` column in the
Milestone 2/3 migration (911ac2a05c12), defaulting every pre-existing row
to ASSIGNED_RECORD_USERS (preserves real current behavior). This module
is where visibility is actually *enforced* -- at query time, not just
stored.

Precondition every caller must already satisfy: the actor has access to
the PARENT Lead/Customer record itself (checked at the route layer via
apply_ownership_filter()/customer_visible_to_actor() before ever calling
into this module) -- visibility here narrows *which notes on an
already-accessible record* the actor may see, it does not substitute for
parent-record access control.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select

from app.extensions import db_session
from app.models.customers import CustomerNote
from app.models.leads import LeadNote

_MAX_BODY_LEN = 8000


def _note_visible_to(note, actor_employee_profile_id_or_staff_id, *, is_management: bool, author_field: str) -> bool:
    if note.visibility == "MANAGEMENT_ONLY":
        return is_management
    if note.visibility == "AUTHOR_ONLY":
        return getattr(note, author_field) == actor_employee_profile_id_or_staff_id or is_management
    return True  # ASSIGNED_RECORD_USERS -- parent-record access already proven by the caller


def list_lead_notes_visible_to(lead_id: uuid.UUID, actor_employee_profile_id: uuid.UUID, *, is_management: bool) -> list[LeadNote]:
    """Filters entirely at the Python layer after a DB-level archived_at
    exclusion -- a hidden note is dropped from the result list completely,
    never included-but-redacted, so neither its existence nor its count
    leaks to an unauthorized caller (list length reflects only what the
    actor may actually see)."""
    stmt = select(LeadNote).where(LeadNote.lead_id == lead_id, LeadNote.archived_at.is_(None)).order_by(LeadNote.created_at.desc())
    all_notes = db_session.execute(stmt).scalars().all()
    return [n for n in all_notes if _note_visible_to(n, actor_employee_profile_id, is_management=is_management, author_field="author_employee_profile_id")]


def list_customer_notes_visible_to(customer_id: uuid.UUID, actor_staff_user_id: uuid.UUID, *, is_management: bool) -> list[CustomerNote]:
    stmt = select(CustomerNote).where(CustomerNote.customer_id == customer_id, CustomerNote.archived_at.is_(None)).order_by(CustomerNote.created_at.desc())
    all_notes = db_session.execute(stmt).scalars().all()
    return [n for n in all_notes if _note_visible_to(n, actor_staff_user_id, is_management=is_management, author_field="author_staff_user_id")]
