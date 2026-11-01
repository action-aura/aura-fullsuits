"""A seller's own ID photo (Alembic SID1).

One row per uploaded photo; the file itself is NOT here. It is an AES-256-GCM
encrypted blob on disk, written by the same code that stores a customer's ID
(app/customer_onboarding/documents.py: same cleaning, same key list, same blob
format). This row says where it is, what it is, how big, its SHA-256 (of the stored
plaintext, so a restore can be checked) and which key encrypted it.

ONE CURRENT PHOTO PER SELLER. A new upload does not overwrite: it stamps the old row
`superseded_at` and adds a new one, so the history of what the company held stays.
The partial unique indexes below are what makes "one current" a database fact and
not only a habit of the code.

WHO IT BELONGS TO. A seller is created from an invitation (employees.create), and
the StaffUser row does not exist until the invitation is accepted. So a photo a
manager attaches at creation time belongs to the INVITATION first (invitation_id) and
is bound to the staff user (staff_user_id) at acceptance. Exactly the rows with a
staff_user_id count for the gate.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPKMixin


class StaffIdentityDocument(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_staff_identity_documents"
    __table_args__ = (
        CheckConstraint("size_bytes > 0", name="ck_owner_staff_identity_documents_size_positive"),
        CheckConstraint(
            "staff_user_id IS NOT NULL OR invitation_id IS NOT NULL", name="ck_owner_staff_identity_documents_has_owner"
        ),
        # One CURRENT photo per seller, and one per not-yet-accepted invitation.
        Index(
            "uq_owner_staff_identity_documents_current_staff", "staff_user_id", unique=True,
            postgresql_where=text("superseded_at IS NULL AND staff_user_id IS NOT NULL"),
        ),
        Index(
            "uq_owner_staff_identity_documents_current_invitation", "invitation_id", unique=True,
            postgresql_where=text("superseded_at IS NULL AND staff_user_id IS NULL"),
        ),
    )

    staff_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id"), index=True
    )
    invitation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_invitations.id"), index=True
    )
    storage_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    content_type: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    enc_key_id: Mapped[str] = mapped_column(String(32), nullable=False)
    uploaded_by_staff_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id")
    )
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Retention (feat/customer-notices N4): a seller's ID photo is kept while they work
    # for us and 365 days after they are disabled; purge_after carries that date and
    # purged_at says the file is gone (the row stays as the record that one existed).
    purge_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
