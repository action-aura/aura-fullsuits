"""Customer organization models (Part D: CUSTOMERS)."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPKMixin


class Customer(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_customers"

    legal_name: Mapped[str] = mapped_column(String(255), nullable=False)
    trade_name: Mapped[str | None] = mapped_column(String(255))
    organization_type: Mapped[str | None] = mapped_column(String(64))
    country: Mapped[str | None] = mapped_column(String(64))
    city: Mapped[str | None] = mapped_column(String(128))
    tax_identifier: Mapped[str | None] = mapped_column(String(128))
    commercial_registration_reference: Mapped[str | None] = mapped_column(String(128))
    primary_language: Mapped[str] = mapped_column(String(8), default="en", nullable=False)
    timezone: Mapped[str | None] = mapped_column(String(64))
    lifecycle_status: Mapped[str] = mapped_column(String(32), default="LEAD", nullable=False)
    acquisition_source: Mapped[str | None] = mapped_column(String(128))
    assigned_sales_staff_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id"), index=True
    )
    assigned_support_staff_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id")
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Phase 9.5A -- traceability back to the Lead this Customer was converted
    # from, when applicable. NULL for a Customer created directly (still
    # fully valid, unchanged Phase 5 behavior -- see
    # docs/owner/phase9_5a/lead-conversion-contract.md). Written exactly once,
    # at conversion time, by LeadConversionService -- never reassigned.
    converted_from_lead_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_leads.id"))
    # Phase 9.5C -- additive optimistic-lock column, same pattern already
    # used by Lead.version and CustomerLocation.version. Defaults to 1 for
    # every pre-existing row via the migration's server_default.
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    contacts: Mapped[list["CustomerContact"]] = relationship(back_populates="customer", cascade="all, delete-orphan")
    addresses: Mapped[list["CustomerAddress"]] = relationship(back_populates="customer", cascade="all, delete-orphan")
    notes: Mapped[list["CustomerNote"]] = relationship(back_populates="customer", cascade="all, delete-orphan")


class CustomerContact(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_customer_contacts"

    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_customers.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str | None] = mapped_column(String(128))
    business_email: Mapped[str | None] = mapped_column(String(255))
    business_phone: Mapped[str | None] = mapped_column(String(64))
    preferred_channel: Mapped[str | None] = mapped_column(String(32))
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Phase 9.5C -- additive.
    notes: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    customer: Mapped[Customer] = relationship(back_populates="contacts")


class CustomerAddress(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_customer_addresses"

    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_customers.id"), nullable=False
    )
    line1: Mapped[str | None] = mapped_column(String(255))
    line2: Mapped[str | None] = mapped_column(String(255))
    city: Mapped[str | None] = mapped_column(String(128))
    country: Mapped[str | None] = mapped_column(String(64))
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    customer: Mapped[Customer] = relationship(back_populates="addresses")


class CustomerNote(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_customer_notes"

    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_customers.id"), nullable=False, index=True
    )
    author_staff_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id"), nullable=False
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    # Phase 9.5C -- additive, same default-preserves-current-behavior
    # rationale as LeadNote.visibility.
    visibility: Mapped[str] = mapped_column(String(32), default="ASSIGNED_RECORD_USERS", nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    customer: Mapped[Customer] = relationship(back_populates="notes")
