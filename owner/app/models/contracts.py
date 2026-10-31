"""New customer flow, slice S5 (Alembic CT1, c5a2e7d94f16): the e-contract.

owner_contract_versions      the text a person must accept. Two audiences:
                             CUSTOMER (the shop owner, at the first sign-in in the app)
                             and SELLER (a staff member who sells, in Owner). A version's
                             text and its SHA-256 never change after it is written (a DB
                             trigger refuses it): fixing a typo means a NEW version, so
                             an old acceptance always points at exactly the words that
                             were shown. The SHA-256 is also CHECKed by the database
                             against the stored text.
owner_contract_acceptances   one row per person per version, APPEND-ONLY (a DB trigger
                             refuses UPDATE and DELETE). It is the evidence: who (email
                             snapshot), which text (sha256), from which device (key
                             fingerprint + the device's own signature over the request),
                             from which address, when.

Neither table holds a password, a token or a licence key.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, Index, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPKMixin

CONTRACT_AUDIENCES = ("CUSTOMER", "SELLER")
CONTRACT_LANGUAGES = ("en", "ar")


class ContractVersion(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_contract_versions"
    __table_args__ = (
        CheckConstraint("audience IN ('CUSTOMER', 'SELLER')", name="ck_owner_contract_versions_audience"),
        CheckConstraint("language IN ('en', 'ar')", name="ck_owner_contract_versions_language"),
        # The hash is part of what a customer signs; the database re-derives it.
        CheckConstraint(
            "body_sha256 = encode(sha256(convert_to(body, 'UTF8')), 'hex')", name="ck_owner_contract_versions_body_sha256"
        ),
        CheckConstraint("retired_at IS NULL OR published_at IS NOT NULL", name="ck_owner_contract_versions_retired_was_published"),
        CheckConstraint(
            "(pdf_storage_key IS NULL) = (pdf_sha256 IS NULL)", name="ck_owner_contract_versions_pdf_all_or_nothing"
        ),
        UniqueConstraint("audience", "language", "version_label", name="uq_owner_contract_versions_label"),
        # At most ONE live version per audience and language: the one a person is shown.
        Index(
            "uq_owner_contract_versions_one_live", "audience", "language", unique=True,
            postgresql_where=text("published_at IS NOT NULL AND retired_at IS NULL"),
        ),
    )

    audience: Mapped[str] = mapped_column(String(16), nullable=False)
    language: Mapped[str] = mapped_column(String(8), nullable=False)
    version_label: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    body_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    # Optional archive copy (the lawyer's PDF), encrypted like the customer documents.
    pdf_storage_key: Mapped[str | None] = mapped_column(String(255))
    pdf_sha256: Mapped[str | None] = mapped_column(String(64))
    pdf_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    pdf_enc_key_id: Mapped[str | None] = mapped_column(String(32))
    lawyer_approved: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    lawyer_approval_note: Mapped[str | None] = mapped_column(Text)
    lawyer_approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lawyer_approved_by_staff_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id")
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_by_staff_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_staff_users.id"))
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_staff_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_staff_users.id"))


class ContractAcceptance(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_contract_acceptances"
    __table_args__ = (
        CheckConstraint("audience IN ('CUSTOMER', 'SELLER')", name="ck_owner_contract_acceptances_audience"),
        # A customer acceptance names a customer (and their account), a seller
        # acceptance names a staff member; never both, never neither.
        CheckConstraint(
            "(audience = 'CUSTOMER' AND customer_id IS NOT NULL AND staff_user_id IS NULL) OR "
            "(audience = 'SELLER' AND staff_user_id IS NOT NULL AND customer_id IS NULL)",
            name="ck_owner_contract_acceptances_who",
        ),
        Index("uq_owner_contract_acceptances_customer", "customer_id", "contract_version_id", unique=True,
              postgresql_where=text("customer_id IS NOT NULL")),
        Index("uq_owner_contract_acceptances_staff", "staff_user_id", "contract_version_id", unique=True,
              postgresql_where=text("staff_user_id IS NOT NULL")),
    )

    contract_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_contract_versions.id"), nullable=False, index=True
    )
    audience: Mapped[str] = mapped_column(String(16), nullable=False)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_customers.id"), index=True)
    account_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_customer_accounts.id"))
    staff_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_staff_users.id"), index=True)
    email_snapshot: Mapped[str] = mapped_column(String(255), nullable=False)
    # The product's own local installation label, when it sent one (the server-side
    # installation does not exist yet at acceptance time: acceptance comes BEFORE activation).
    installation_id: Mapped[str | None] = mapped_column(String(128))
    device_key_fingerprint: Mapped[str | None] = mapped_column(String(64))
    device_signature: Mapped[str | None] = mapped_column(Text)
    ip: Mapped[str | None] = mapped_column(String(64))
    app_version: Mapped[str | None] = mapped_column(String(64))
    language: Mapped[str] = mapped_column(String(8), nullable=False)
    text_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
