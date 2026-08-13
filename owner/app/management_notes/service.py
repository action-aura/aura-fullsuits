"""Phase 9.5E -- Management Notes service. Genuinely new (Milestone 1's
audit: schema existed since 9.5A, zero service layer). Reuses the real
existing enum values as-is (MANAGEMENT_ONLY/SPECIFIC_EMPLOYEES/ALL_STAFF) --
per the reuse-matrix's own commitment, this does not invent new visibility
categories beyond what the 9.5A schema already defines."""
from __future__ import annotations

import uuid

from sqlalchemy import select

from app.audit.services import record as audit_record
from app.extensions import db_session
from app.management_notes.errors import MANAGEMENT_NOTE_TRANSITIONS, ManagementNoteError
from app.models.base import utcnow
from app.models.management_notes import (
    MANAGEMENT_NOTE_VISIBILITIES,
    ManagementNoteComment,
    ManagementNoteVisibilityGrant,
    SharedManagementNote,
)


def _check_transition(status: str, target: str) -> None:
    if target not in MANAGEMENT_NOTE_TRANSITIONS.get(status, set()):
        raise ManagementNoteError("INVALID_MANAGEMENT_NOTE_TRANSITION", from_status=status, to_status=target)


def create_note(
    *,
    title: str,
    body: str,
    category: str | None,
    priority: str,
    visibility: str,
    assigned_employee_profile_id: uuid.UUID | None,
    due_date=None,
    specific_employee_profile_ids: list[uuid.UUID] | None = None,
    created_by_staff_user_id: uuid.UUID,
) -> SharedManagementNote:
    if not title or not title.strip():
        raise ManagementNoteError("TITLE_REQUIRED")
    if not body or not body.strip():
        raise ManagementNoteError("BODY_REQUIRED")
    if visibility not in MANAGEMENT_NOTE_VISIBILITIES:
        raise ManagementNoteError("VISIBILITY_INVALID")
    if visibility == "SPECIFIC_EMPLOYEES" and not specific_employee_profile_ids:
        raise ManagementNoteError("SPECIFIC_EMPLOYEES_REQUIRES_GRANTS")

    note = SharedManagementNote(
        title=title, body=body, category=category, priority=priority, status="OPEN", pinned=False,
        created_by_staff_user_id=created_by_staff_user_id, assigned_employee_profile_id=assigned_employee_profile_id,
        visibility=visibility, due_date=due_date,
    )
    db_session.add(note)
    db_session.flush()

    if visibility == "SPECIFIC_EMPLOYEES":
        for employee_profile_id in specific_employee_profile_ids:
            db_session.add(ManagementNoteVisibilityGrant(management_note_id=note.id, employee_profile_id=employee_profile_id))

    db_session.commit()

    audit_record(
        actor_staff_user_id=created_by_staff_user_id, actor_role_snapshot=None,
        action_code="MANAGEMENT_NOTE_CREATED", entity_type="management_note", entity_public_id=str(note.id),
        after_state={"visibility": visibility, "status": "OPEN"},
    )
    return note


def update_note(
    note: SharedManagementNote, *, actor_staff_user_id: uuid.UUID, expected_version: int | None = None,
    title: str | None = None, body: str | None = None, category: str | None = None, priority: str | None = None,
    due_date=None,
) -> SharedManagementNote:
    if expected_version is not None and expected_version != note.version:
        raise ManagementNoteError("STALE_VERSION")
    if title is not None:
        note.title = title
    if body is not None:
        note.body = body
    if category is not None:
        note.category = category
    if priority is not None:
        note.priority = priority
    if due_date is not None:
        note.due_date = due_date
    note.updated_by_staff_user_id = actor_staff_user_id
    note.version += 1
    db_session.commit()

    audit_record(
        actor_staff_user_id=actor_staff_user_id, actor_role_snapshot=None,
        action_code="MANAGEMENT_NOTE_UPDATED", entity_type="management_note", entity_public_id=str(note.id),
        after_state={"version": note.version},
    )
    return note


def assign_note(note: SharedManagementNote, *, assignee_employee_profile_id: uuid.UUID | None, actor_staff_user_id: uuid.UUID) -> SharedManagementNote:
    note.assigned_employee_profile_id = assignee_employee_profile_id
    note.updated_by_staff_user_id = actor_staff_user_id
    note.version += 1
    db_session.commit()

    audit_record(
        actor_staff_user_id=actor_staff_user_id, actor_role_snapshot=None,
        action_code="MANAGEMENT_NOTE_ASSIGNED", entity_type="management_note", entity_public_id=str(note.id),
        after_state={"assigned_employee_profile_id": str(assignee_employee_profile_id) if assignee_employee_profile_id else None},
    )
    return note


def set_note_status(note: SharedManagementNote, *, target_status: str, actor_staff_user_id: uuid.UUID) -> SharedManagementNote:
    _check_transition(note.status, target_status)
    note.status = target_status
    note.updated_by_staff_user_id = actor_staff_user_id
    note.version += 1
    if target_status == "ARCHIVED":
        note.archived_at = utcnow()
    db_session.commit()

    audit_record(
        actor_staff_user_id=actor_staff_user_id, actor_role_snapshot=None,
        action_code="MANAGEMENT_NOTE_STATUS_CHANGED", entity_type="management_note", entity_public_id=str(note.id),
        after_state={"status": target_status},
    )
    return note


def add_comment(note: SharedManagementNote, *, body: str, author_staff_user_id: uuid.UUID) -> ManagementNoteComment:
    if not body or not body.strip():
        raise ManagementNoteError("BODY_REQUIRED")
    comment = ManagementNoteComment(management_note_id=note.id, author_staff_user_id=author_staff_user_id, body=body)
    db_session.add(comment)
    db_session.commit()

    audit_record(
        actor_staff_user_id=author_staff_user_id, actor_role_snapshot=None,
        action_code="MANAGEMENT_NOTE_COMMENT_ADDED", entity_type="management_note", entity_public_id=str(note.id),
    )
    return comment


def can_view_note(note: SharedManagementNote, *, actor_employee_profile_id: uuid.UUID | None, has_manage_permission: bool) -> bool:
    """The one real authorization decision this domain makes -- checked by
    every route/query before a note or its existence is ever revealed
    (list filters must apply this too, not just detail-view routes, to
    avoid count/search/pagination leakage)."""
    if has_manage_permission:
        return True
    if note.visibility == "ALL_STAFF":
        return True
    if actor_employee_profile_id is None:
        return False
    if note.assigned_employee_profile_id == actor_employee_profile_id:
        return True
    if note.visibility == "SPECIFIC_EMPLOYEES":
        grant = db_session.execute(
            select(ManagementNoteVisibilityGrant).where(
                ManagementNoteVisibilityGrant.management_note_id == note.id,
                ManagementNoteVisibilityGrant.employee_profile_id == actor_employee_profile_id,
            )
        ).scalars().first()
        return grant is not None
    return False  # MANAGEMENT_ONLY and no manage permission


def notes_visible_to(*, actor_employee_profile_id: uuid.UUID | None, has_manage_permission: bool) -> list[SharedManagementNote]:
    """Query-level filtering -- never fetch-then-filter-in-Python for a real
    list endpoint (that would still leak via pagination/counts), used here
    for the common case; the same can_view_note() predicate is what a route
    must also apply to any detail/comment/count endpoint."""
    if has_manage_permission:
        return db_session.execute(select(SharedManagementNote).order_by(SharedManagementNote.created_at.desc())).scalars().all()

    all_staff = select(SharedManagementNote).where(SharedManagementNote.visibility == "ALL_STAFF")
    if actor_employee_profile_id is None:
        return db_session.execute(all_staff).scalars().all()

    assigned = select(SharedManagementNote).where(SharedManagementNote.assigned_employee_profile_id == actor_employee_profile_id)
    granted_ids = select(ManagementNoteVisibilityGrant.management_note_id).where(
        ManagementNoteVisibilityGrant.employee_profile_id == actor_employee_profile_id
    )
    granted = select(SharedManagementNote).where(SharedManagementNote.id.in_(granted_ids))

    seen: dict[uuid.UUID, SharedManagementNote] = {}
    for stmt in (all_staff, assigned, granted):
        for note in db_session.execute(stmt).scalars().all():
            seen[note.id] = note
    return sorted(seen.values(), key=lambda n: n.created_at, reverse=True)
