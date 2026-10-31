"""The seller's own ID photo: store, replace, read.

NO SECOND CRYPTO PATH. Cleaning (type from the bytes, metadata stripped, 10 MB cap),
the key list (OWNER_KYC_KEYS), the blob format (AKYC1 + wrapped DEK + AES-256-GCM) and
the storage directory are the customer-ID code in
app/customer_onboarding/documents.py, called as it is. What is different here:

  * the AAD subject. A customer blob is bound to (document id, customer id). A seller
    photo may be uploaded before the staff user exists (a manager attaches it when
    creating the seller; it belongs to the invitation until acceptance), so the owner
    id cannot be part of the AAD. The constant below takes its place: a blob still
    cannot be swapped between documents (the document id is in the AAD), and a seller
    ID blob cannot be read back as a customer document or the other way round.
  * supersede, not overwrite. A new photo stamps the old row `superseded_at`.

The CALLER commits (same convention as customer documents): the row and the rest of
its action are one transaction; discard_blob() removes the file of a row that was
never committed.
"""
from __future__ import annotations

import hashlib
import os
import uuid
from datetime import datetime, timezone

from sqlalchemy import select, update

from app.customer_onboarding import documents as kyc
from app.customer_onboarding.documents import DocumentError  # noqa: F401 -- re-exported for the routes
from app.extensions import db_session
from app.models.staff_identity import StaffIdentityDocument

# Stands where the customer id stands in the customer-document AAD (see module docstring).
AAD_SUBJECT = "staff-identity-photo"
STORAGE_DIR = "staff-id"

# Only a picture is an ID photo here. The shared cleaner also accepts a PDF (a customer
# may hand over a scanned registration); a seller's own ID photo is a picture they or a
# manager took, so a PDF is refused.
ALLOWED_CONTENT_TYPES = frozenset({"image/jpeg", "image/png"})


def storage_is_ready() -> bool:
    return kyc.storage_is_ready()


def prepare(content: bytes) -> tuple[bytes, str]:
    """Validates and cleans an upload WITHOUT touching disk or the database. Raises
    DocumentError (EMPTY / TOO_LARGE / TYPE_NOT_ALLOWED / CORRUPT)."""
    cleaned, content_type = kyc.clean_file(content)
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise kyc.DocumentError("DOCUMENT_TYPE_NOT_ALLOWED")
    return cleaned, content_type


def store_identity_photo(
    *, content: bytes, uploaded_by_staff_user_id, staff_user_id=None, invitation_id=None, now: datetime | None = None
) -> StaffIdentityDocument:
    """Cleans, encrypts, writes the blob and adds the row (superseding the current one
    for the same owner). Exactly one of staff_user_id / invitation_id is given."""
    if (staff_user_id is None) == (invitation_id is None):
        raise ValueError("give exactly one of staff_user_id / invitation_id")
    cleaned, content_type = prepare(content)
    kid, kek = kyc.active_key()
    now = now or datetime.now(timezone.utc)
    document_id = uuid.uuid4()
    blob = kyc.seal(cleaned, document_id=document_id, customer_id=AAD_SUBJECT, kid=kid, kek=kek)
    storage_key = f"{STORAGE_DIR}/{document_id}.bin"
    final_path = kyc.path_for_key(storage_key)
    os.makedirs(os.path.dirname(final_path), exist_ok=True)
    tmp_path = final_path + ".part"
    with open(tmp_path, "wb") as handle:
        handle.write(blob)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp_path, final_path)

    owner_filter = (
        StaffIdentityDocument.staff_user_id == staff_user_id if staff_user_id is not None
        else StaffIdentityDocument.invitation_id == invitation_id
    )
    db_session.execute(
        update(StaffIdentityDocument)
        .where(owner_filter, StaffIdentityDocument.superseded_at.is_(None))
        .values(superseded_at=now)
    )
    document = StaffIdentityDocument(
        id=document_id, staff_user_id=staff_user_id, invitation_id=invitation_id, storage_key=storage_key,
        content_type=content_type, size_bytes=len(cleaned), sha256=hashlib.sha256(cleaned).hexdigest(), enc_key_id=kid,
        uploaded_by_staff_user_id=uploaded_by_staff_user_id, uploaded_at=now,
    )
    db_session.add(document)
    return document


def discard_blob(document: StaffIdentityDocument) -> None:
    """Removes the file of a row that was never committed (a refused or rolled-back upload)."""
    try:
        os.remove(kyc.path_for_key(document.storage_key))
    except (OSError, kyc.DocumentError):
        pass


def read_photo(document: StaffIdentityDocument) -> tuple[bytes, str]:
    """(plaintext, content type). The caller has ALREADY checked permission."""
    kek = kyc.key_for(document.enc_key_id)
    if kek is None:
        raise kyc.DocumentError("KYC_KEY_UNAVAILABLE")
    path = kyc.path_for_key(document.storage_key)
    if not os.path.isfile(path):
        raise kyc.DocumentError("DOCUMENT_UNREADABLE")
    with open(path, "rb") as handle:
        blob = handle.read()
    plaintext = kyc.open_blob(blob, document_id=document.id, customer_id=AAD_SUBJECT, kek=kek)
    if hashlib.sha256(plaintext).hexdigest() != document.sha256:
        raise kyc.DocumentError("DOCUMENT_UNREADABLE")
    return plaintext, document.content_type


def current_photo(staff_user_id) -> StaffIdentityDocument | None:
    return db_session.execute(
        select(StaffIdentityDocument).where(
            StaffIdentityDocument.staff_user_id == staff_user_id, StaffIdentityDocument.superseded_at.is_(None)
        )
    ).scalars().first()


def current_photo_of_invitation(invitation_id) -> StaffIdentityDocument | None:
    return db_session.execute(
        select(StaffIdentityDocument).where(
            StaffIdentityDocument.invitation_id == invitation_id, StaffIdentityDocument.superseded_at.is_(None)
        )
    ).scalars().first()


def has_current_photo(staff_user_id) -> bool:
    return current_photo(staff_user_id) is not None


def bind_invitation_photo(invitation_id, staff_user_id) -> int:
    """Acceptance: the photo a manager attached to the invitation now belongs to the new
    staff user (so it satisfies the gate at their first sign-in). Runs inside the
    acceptance transaction; the caller commits."""
    result = db_session.execute(
        update(StaffIdentityDocument)
        .where(StaffIdentityDocument.invitation_id == invitation_id, StaffIdentityDocument.staff_user_id.is_(None))
        .values(staff_user_id=staff_user_id)
    )
    return result.rowcount


def move_invitation_photo(old_invitation_id, new_invitation_id) -> int:
    """Re-issue: the old invitation is revoked and a fresh one created for the same
    person; the photo follows. The caller commits."""
    result = db_session.execute(
        update(StaffIdentityDocument)
        .where(StaffIdentityDocument.invitation_id == old_invitation_id, StaffIdentityDocument.staff_user_id.is_(None))
        .values(invitation_id=new_invitation_id)
    )
    return result.rowcount
