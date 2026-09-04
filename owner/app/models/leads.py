"""Phase 9.5A Milestone 6/7/8 -- lead pipeline, customer location, and shared
interaction/followup history.

Lead is a genuinely new, separate table from Customer (whose
lifecycle_status="LEAD" default is left untouched -- see
docs/owner/phase9_5a/duplication-risk-report.md). customer_locations is a
shared table for Lead and Customer (exactly one of lead_id/customer_id set),
so a location captured before conversion is never lost or duplicated after.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPKMixin

LEAD_STATUSES = (
    "NEW", "NOT_INTERESTED_NOW", "POTENTIAL", "FOLLOW_UP", "UNDER_OBSERVATION",
    "QUALIFIED", "CONFIRMED", "LOST", "ARCHIVED",
)
LEAD_SOURCES = ("WEBSITE", "REFERRAL", "COLD_OUTREACH", "EVENT", "OTHER", "MAPS_DISCOVERY")  # MAPS_DISCOVERY: imported from the map-search discovery feature
LEAD_PRIORITIES = ("LOW", "MEDIUM", "HIGH")
LOCATION_SOURCES = ("GPS", "NETWORK", "MANUAL", "IMPORTED")
INTERACTION_TYPES = ("CALL", "EMAIL", "MEETING", "WHATSAPP_MANUAL_NOTE", "OTHER")


class Lead(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_leads"

    organization_or_prospect_name: Mapped[str] = mapped_column(String(200), nullable=False)
    primary_contact_name: Mapped[str | None] = mapped_column(String(200))
    phone: Mapped[str | None] = mapped_column(String(32))
    email: Mapped[str | None] = mapped_column(String(200))
    source: Mapped[str] = mapped_column(String(24), default="OTHER", nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="NEW", nullable=False)
    assigned_employee_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_employee_profiles.id"), index=True
    )
    created_by_employee_profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_employee_profiles.id"), nullable=False, index=True
    )
    estimated_value: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    currency: Mapped[str | None] = mapped_column(String(3))
    priority: Mapped[str] = mapped_column(String(8), default="MEDIUM", nullable=False)
    next_follow_up_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_interaction_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    location_summary: Mapped[str | None] = mapped_column(String(200))
    converted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lost_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class LeadProductInterest(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_lead_product_interests"

    lead_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_leads.id"), nullable=False)
    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_products.id"), nullable=False)


class LeadStatusHistory(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_lead_status_history"

    lead_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_leads.id"), nullable=False, index=True)
    from_status: Mapped[str | None] = mapped_column(String(24))
    to_status: Mapped[str] = mapped_column(String(24), nullable=False)
    changed_by_employee_profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_employee_profiles.id"), nullable=False
    )
    reason: Mapped[str | None] = mapped_column(Text)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LeadAssignment(Base, UUIDPKMixin, TimestampMixin):
    """Current assignment = the row with unassigned_at IS NULL. Reassignment
    closes the old row and opens a new one -- never an in-place update
    (docs/owner/phase9_5a/lead-customer-domain-model.md)."""

    __tablename__ = "owner_lead_assignments"

    lead_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_leads.id"), nullable=False, index=True)
    assigned_to_employee_profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_employee_profiles.id"), nullable=False
    )
    assigned_by_employee_profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_employee_profiles.id"), nullable=False
    )
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    unassigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str | None] = mapped_column(Text)


class LeadInteraction(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_lead_interactions"

    lead_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_leads.id"), nullable=False, index=True)
    employee_profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_employee_profiles.id"), nullable=False, index=True
    )
    interaction_type: Mapped[str] = mapped_column(String(24), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LeadFollowup(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_lead_followups"

    lead_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_leads.id"), nullable=False, index=True)
    employee_profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_employee_profiles.id"), nullable=False, index=True
    )
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Phase 9.5C -- additive. Without this, "not completed_at" cannot
    # distinguish a still-open follow-up from a cancelled one; OVERDUE
    # itself stays derived (status == OPEN and due_at < now), never stored.
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)


class LeadNote(Base, UUIDPKMixin, TimestampMixin):
    """Deliberately separate from CustomerNote (a Lead is not a Customer) and
    from SharedManagementNote (this is lead-scoped, that is not)."""

    __tablename__ = "owner_lead_notes"

    lead_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_leads.id"), nullable=False, index=True)
    author_employee_profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_employee_profiles.id"), nullable=False
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    # Phase 9.5C -- additive. Default preserves pre-existing real-world
    # behavior for every row written before this column existed: today,
    # anyone with record access already sees every note, which is exactly
    # what ASSIGNED_RECORD_USERS means. See crm-note-visibility-contract.md.
    visibility: Mapped[str] = mapped_column(String(32), default="ASSIGNED_RECORD_USERS", nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LeadContact(Base, UUIDPKMixin, TimestampMixin):
    """Phase 9.5C Milestone 8 -- Leads have no existing contact concept
    (unlike Customer, which already has CustomerContact), so this is a
    genuinely new table -- not a second authority for the same concept.
    Field shape/primary-flag rule intentionally copied from CustomerContact
    exactly, so conversion (Milestone 13) can carry a Lead's contacts across
    to CustomerContact rows with a simple 1:1 field mapping."""

    __tablename__ = "owner_lead_contacts"

    lead_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_leads.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str | None] = mapped_column(String(128))
    business_email: Mapped[str | None] = mapped_column(String(255))
    business_phone: Mapped[str | None] = mapped_column(String(64))
    preferred_channel: Mapped[str | None] = mapped_column(String(32))
    is_primary: Mapped[bool] = mapped_column(default=False, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CustomerLocation(Base, UUIDPKMixin, TimestampMixin):
    """Shared table for Lead and Customer -- exactly one of lead_id/customer_id
    is set (CHECK constraint), never both, never neither. See
    docs/owner/phase9_5a/customer-location-contract.md."""

    __tablename__ = "owner_customer_locations"
    __table_args__ = (
        CheckConstraint(
            "(lead_id IS NOT NULL)::int + (customer_id IS NOT NULL)::int = 1",
            name="ck_customer_locations_exactly_one_owner",
        ),
    )

    lead_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_leads.id"), index=True)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_customers.id"), index=True)
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    accuracy_meters: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    captured_by_employee_profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_employee_profiles.id"), nullable=False
    )
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    manual_address: Mapped[str | None] = mapped_column(Text)
    reverse_geocoded_address: Mapped[str | None] = mapped_column(Text)
    verified: Mapped[bool] = mapped_column(default=False, nullable=False)
    verification_method: Mapped[str | None] = mapped_column(String(64))
    # Phase 9.5C -- additive. verified/verification_method already existed
    # (Phase 9.5A) but nothing recorded WHO verified it, WHEN, or WHY --
    # required by this milestone's "verification requires reason and audit."
    verified_by_employee_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_employee_profiles.id")
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verification_reason: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CustomerInteraction(Base, UUIDPKMixin, TimestampMixin):
    """Post-conversion interaction history -- same shape as LeadInteraction,
    kept as a separate table since a Customer's history is distinct from its
    pre-conversion Lead history (the Lead row itself is retained, linked via
    Customer.converted_from_lead_id)."""

    __tablename__ = "owner_customer_interactions"

    customer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_customers.id"), nullable=False, index=True)
    employee_profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_employee_profiles.id"), nullable=False
    )
    interaction_type: Mapped[str] = mapped_column(String(24), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CustomerFollowup(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_customer_followups"

    customer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_customers.id"), nullable=False, index=True)
    employee_profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_employee_profiles.id"), nullable=False
    )
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Phase 9.5C -- additive. Without this, "not completed_at" cannot
    # distinguish a still-open follow-up from a cancelled one; OVERDUE
    # itself stays derived (status == OPEN and due_at < now), never stored.
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)


class CustomerAssignment(Base, UUIDPKMixin, TimestampMixin):
    """Phase 9.5C -- mirrors LeadAssignment's exact append-only close/open
    pattern. Customer.assigned_sales_staff_id remains the live pointer
    (unchanged); this table is the new history record behind reassignment."""

    __tablename__ = "owner_customer_assignments"

    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_customers.id"), nullable=False, index=True
    )
    assigned_to_staff_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id"), nullable=False
    )
    assigned_by_staff_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id"), nullable=False
    )
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    unassigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str | None] = mapped_column(Text)
