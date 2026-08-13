"""Subscription, renewal, and payment models (Part D: SUBSCRIPTIONS AND COMMERCIAL RECORDS)."""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPKMixin


class Subscription(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_subscriptions"
    # Real backstop against concurrent fulfill_order() calls both creating a
    # Subscription for the same order (migration 5de3f36f4c21) -- nullable-
    # safe, Postgres allows unlimited NULLs in a unique column, so this never
    # blocks a Subscription created outside fulfillment (sales_order_id NULL).
    __table_args__ = (UniqueConstraint("sales_order_id", name="uq_subscriptions_one_per_sales_order"),)

    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_customers.id"), nullable=False
    )
    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_products.id"), nullable=False)
    plan_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_plans.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="DRAFT", nullable=False)
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    billing_cycle: Mapped[str | None] = mapped_column(String(32))
    auto_renew_preference: Mapped[bool] = mapped_column(default=False, nullable=False)
    device_allowance: Mapped[int | None] = mapped_column()
    platform_allowance: Mapped[str | None] = mapped_column(String(64))
    renewal_date: Mapped[date | None] = mapped_column(Date)
    cancellation_date: Mapped[date | None] = mapped_column(Date)
    cancellation_reason: Mapped[str | None] = mapped_column(Text)
    sales_owner_staff_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id")
    )
    support_owner_staff_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id")
    )
    internal_notes: Mapped[str | None] = mapped_column(Text)
    created_by_staff_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id")
    )
    approved_by_staff_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id")
    )
    # Phase 9.5A -- additive fulfillment bookkeeping link to the SalesOrder
    # this subscription was issued for, when applicable. NULL for a
    # subscription created directly (unchanged Phase 6/8 behavior). Does not
    # change any existing subscription-issuance condition -- see
    # docs/owner/phase9_5a/payment-and-fulfillment-contract.md.
    sales_order_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_sales_orders.id"))

    customer: Mapped["Customer"] = relationship()  # noqa: F821
    product: Mapped["Product"] = relationship()  # noqa: F821
    plan: Mapped["Plan"] = relationship()  # noqa: F821
    items: Mapped[list["SubscriptionItem"]] = relationship(back_populates="subscription", cascade="all, delete-orphan")
    addons: Mapped[list["SubscriptionAddon"]] = relationship(
        back_populates="subscription", cascade="all, delete-orphan"
    )
    # No delete-orphan here, deliberately: status_history is an audit trail
    # (Part T) -- it must never silently vanish as a side effect of deleting
    # its parent subscription (no route does this today, but the ORM
    # relationship itself should not make that easy in the future either).
    status_history: Mapped[list["SubscriptionStatusHistory"]] = relationship(back_populates="subscription")


class SubscriptionItem(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_subscription_items"

    subscription_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_subscriptions.id"), nullable=False
    )
    description: Mapped[str] = mapped_column(String(255), nullable=False)
    quantity: Mapped[int] = mapped_column(default=1, nullable=False)

    subscription: Mapped[Subscription] = relationship(back_populates="items")


class SubscriptionAddon(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_subscription_addons"

    subscription_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_subscriptions.id"), nullable=False
    )
    addon_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_addons.id"), nullable=False)

    subscription: Mapped[Subscription] = relationship(back_populates="addons")
    addon: Mapped["Addon"] = relationship()  # noqa: F821


class SubscriptionStatusHistory(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_subscription_status_history"

    subscription_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_subscriptions.id"), nullable=False
    )
    from_status: Mapped[str | None] = mapped_column(String(32))
    to_status: Mapped[str] = mapped_column(String(32), nullable=False)
    changed_by_staff_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id")
    )
    reason: Mapped[str | None] = mapped_column(Text)

    subscription: Mapped[Subscription] = relationship(back_populates="status_history")


class RenewalRecord(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_renewal_records"

    subscription_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_subscriptions.id"), nullable=False
    )
    previous_end_date: Mapped[date | None] = mapped_column(Date)
    new_end_date: Mapped[date] = mapped_column(Date, nullable=False)
    previous_plan_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_plans.id"))
    new_plan_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_plans.id"))
    reason: Mapped[str | None] = mapped_column(Text)
    approved_by_staff_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id")
    )
    related_payment_record_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_payment_records.id")
    )

    subscription: Mapped[Subscription] = relationship()


class PaymentRecord(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_payment_records"

    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_customers.id"), nullable=False
    )
    subscription_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_subscriptions.id")
    )
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    method: Mapped[str | None] = mapped_column(String(64))
    payment_date: Mapped[date] = mapped_column(Date, nullable=False)
    reference: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), default="PENDING", nullable=False)
    recorded_by_staff_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id")
    )
    verified_by_staff_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id")
    )
    internal_note: Mapped[str | None] = mapped_column(Text)
    # Phase 9.5A -- additive link to the new commercial-invoice document
    # this payment confirms, when applicable. NULL for a payment recorded
    # against a subscription directly (unchanged Phase 8 behavior -- this
    # column is purely additive, PaymentRecord remains the one real payment
    # table, never duplicated -- see
    # docs/owner/phase9_5a/commercial-document-lifecycle.md).
    commercial_invoice_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_commercial_invoices.id")
    )

    customer: Mapped["Customer"] = relationship()  # noqa: F821
    subscription: Mapped[Subscription | None] = relationship()
    # No delete-orphan (Phase 8 Part F: correction history is an audit
    # trail, must never silently vanish if a PaymentRecord row is ever
    # deleted -- same reasoning as every other *_status_history table in
    # this codebase). Defined in app/models/commercial_ops.py; referenced
    # here by string to avoid a circular import between the two modules.
    correction_history: Mapped[list["PaymentCorrectionHistory"]] = relationship(  # noqa: F821
        back_populates="payment_record"
    )
