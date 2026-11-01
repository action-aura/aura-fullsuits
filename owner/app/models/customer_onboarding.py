"""New customer flow, slice S2 (Alembic NC1, e2c4a8b1f730).

Two tables:

  owner_customer_documents   the customer's ID photo, commercial-registration
                             photo and (S3) the payment slip. The file itself is
                             NOT here: it is an AES-256-GCM encrypted blob on disk
                             (app/customer_onboarding/documents.py); this row says
                             where, what, how big, its SHA-256 (of the stored
                             plaintext, so a restore can be checked) and which key
                             encrypted it. Only a Sales Manager or Super Admin may
                             read the file back; the seller who uploaded it cannot.
  owner_trial_device_marks   a physical device (its device-key fingerprint) that
                             has started a trial. One trial per device, ever: a
                             second customer's trial cannot start on the same
                             hardware. UNIQUE on the fingerprint is the guard.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPKMixin

DOCUMENT_KINDS = ("NATIONAL_ID", "COMMERCIAL_REGISTRATION", "PAYMENT_SLIP")


class CustomerDocument(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_customer_documents"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('NATIONAL_ID', 'COMMERCIAL_REGISTRATION', 'PAYMENT_SLIP')", name="ck_owner_customer_documents_kind"
        ),
        # A payment slip belongs to exactly one payment; an ID or registration
        # photo to none. Spelled IS [NOT] NULL, never "= NULL".
        CheckConstraint(
            "(kind = 'PAYMENT_SLIP' AND payment_record_id IS NOT NULL) OR "
            "(kind <> 'PAYMENT_SLIP' AND payment_record_id IS NULL)",
            name="ck_owner_customer_documents_slip_has_payment",
        ),
        CheckConstraint("size_bytes > 0", name="ck_owner_customer_documents_size_positive"),
    )

    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_customers.id"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    payment_record_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_payment_records.id"), index=True
    )
    storage_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    content_type: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    enc_key_id: Mapped[str] = mapped_column(String(32), nullable=False)
    uploaded_by_staff_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id")
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    purge_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TrialDeviceMark(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_trial_device_marks"

    device_key_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    registry_device_id: Mapped[str | None] = mapped_column(String(64))
    # NULL once the customer behind the mark has been purged (documents.purge_due_customers):
    # the fingerprint stays (fraud prevention, disclosed in the privacy notice), the link
    # to the person goes. A NULL-customer mark still BLOCKS every customer (trial.py).
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_customers.id"), nullable=True, index=True
    )
    installation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_installations.id"))
