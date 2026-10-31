"""Saving a seller's ID photo: the one place the three entry points share.

  seller uploads their own    seller_id.upload            (routes.py)
  manager attaches at create  employees.create           (invitation_id until acceptance)
  manager replaces on edit    employees.edit              (staff_user_id)

Every path validates BEFORE it writes anything, commits the row, and only then writes
the audit row (customer-document convention: an audit row never describes a photo that
a failed commit threw away).
"""
from __future__ import annotations

from sqlalchemy.exc import IntegrityError

from app.audit.services import record as audit_record
from app.extensions import db_session
from app.models.staff_identity import StaffIdentityDocument
from app.seller_id import documents

UPLOADED_ACTION = "STAFF_ID_PHOTO_UPLOADED"
VIEWED_ACTION = "STAFF_ID_PHOTO_VIEWED"
ENTITY_TYPE = "staff_identity_document"


class PhotoError(ValueError):
    """A refusal with a stable code; seller_id/messages.py turns it into words."""

    def __init__(self, code: str, **params):
        self.code = code
        self.params = params
        super().__init__(code)


def precheck(content: bytes) -> None:
    """Everything that can be known WITHOUT writing: an encryption key is configured and
    the bytes are a clean JPEG/PNG within the size limit. The create-seller form calls
    this before it makes the invitation, so a bad photo leaves nothing behind."""
    if not documents.storage_is_ready():
        raise PhotoError("KYC_NOT_READY")
    try:
        documents.prepare(content)
    except documents.DocumentError as exc:
        raise PhotoError(exc.code, **exc.params) from exc


def save_photo(
    *, content: bytes, uploaded_by_staff_user_id, staff_user_id=None, invitation_id=None
) -> StaffIdentityDocument:
    precheck(content)
    document = None
    try:
        document = documents.store_identity_photo(
            content=content, uploaded_by_staff_user_id=uploaded_by_staff_user_id,
            staff_user_id=staff_user_id, invitation_id=invitation_id,
        )
        db_session.commit()
    except documents.DocumentError as exc:
        db_session.rollback()
        raise PhotoError(exc.code, **exc.params) from exc
    except IntegrityError as exc:  # two uploads for one seller at the same instant: the unique index decided
        db_session.rollback()
        if document is not None:
            documents.discard_blob(document)
        raise PhotoError("UPLOAD_CONFLICT") from exc
    except Exception:
        db_session.rollback()
        if document is not None:
            documents.discard_blob(document)
        raise
    audit_record(
        actor_staff_user_id=uploaded_by_staff_user_id, actor_role_snapshot=None, action_code=UPLOADED_ACTION,
        entity_type=ENTITY_TYPE, entity_public_id=str(document.id),
        after_state={
            "staff_user_id": str(staff_user_id) if staff_user_id else None,
            "invitation_id": str(invitation_id) if invitation_id else None,
            "size_bytes": document.size_bytes, "content_type": document.content_type,
        },
    )
    return document
