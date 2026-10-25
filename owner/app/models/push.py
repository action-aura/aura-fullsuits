"""Web Push to managers' phones (Step 1, 2026-10-25) -- the three tables.

  owner_push_subscriptions  one row per staff DEVICE that turned
                            notifications on (the browser's PushSubscription:
                            endpoint + the two keys the payload is encrypted
                            to). The endpoint is a capability URL -- anyone who
                            has it can wake that phone -- so it is stored here
                            only, never logged, never put in an audit row,
                            never sent back to any page.
  owner_push_outbox         one row per sale waiting for a manager, written in
                            the SAME transaction as the sale's PENDING payment
                            (app/push/outbox.py::enqueue_sale_waiting): a crash
                            can neither lose the notification (the payment
                            committed, so did this row) nor invent one (the
                            payment rolled back, so did this row). Unique per
                            (event_type, sale).
  owner_push_deliveries     one row per (outbox row, device), made by the
                            worker when it resolves who may confirm the sale
                            AT SEND TIME. Unique, so a re-run, a second worker
                            or a retry never sends one sale to one device twice.

The state machines (app/push/worker.py):

  outbox     PENDING --worker--> EXPANDED   deliveries made for every eligible device
                     +---------> SKIPPED    the sale was already confirmed/rejected,
                                            or the row is older than a day
  delivery   QUEUED --sent-----> SENT
                    --410/404--> FAILED_PERMANENT  (the device unsubscribed: its row is deleted)
                    --4xx------> FAILED_PERMANENT  (the push service refused the message)
                    --429/5xx--> QUEUED with backoff, FAILED_PERMANENT after the last attempt
                    --stale----> CANCELLED  (sale no longer waiting / recipient lost the
                                             right to confirm / staff deactivated)

There is no SENDING state on a delivery: the per-DEVICE lease on the
subscription row (lease_expires_at) is what stops two workers sending to one
phone at once, and it is also the rate limit's anchor (last_sent_at). A crash
between the push service accepting a message and this process committing SENT
re-sends it once after the lease expires -- the same deliberate simplification
app/mail/outbox.py documents; the notification's fixed tag makes the phone
REPLACE the shown notification rather than show a second one.

`last_error` is a short code ("HTTP_503", "URLError"), never a response body
or an exception message: either can echo the endpoint.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPKMixin

OUTBOX_STATUSES = ("PENDING", "EXPANDED", "SKIPPED")
DELIVERY_STATUSES = ("QUEUED", "SENT", "FAILED_PERMANENT", "CANCELLED")


class PushSubscription(Base, UUIDPKMixin):
    __tablename__ = "owner_push_subscriptions"
    __table_args__ = (UniqueConstraint("endpoint", name="uq_owner_push_subscriptions_endpoint"),)

    staff_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id"), nullable=False, index=True
    )
    endpoint: Mapped[str] = mapped_column(Text, nullable=False)
    # The browser's P-256 public key (65 bytes) and auth secret (16 bytes),
    # base64url without padding -- exactly as PushSubscription.toJSON() gives them.
    p256dh: Mapped[str] = mapped_column(String(128), nullable=False)
    auth: Mapped[str] = mapped_column(String(64), nullable=False)
    # The page language when the device subscribed; used only when the staff
    # account itself has no saved language (StaffUser.locale).
    locale: Mapped[str | None] = mapped_column(String(8))
    user_agent: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    # When the PAGE last saved this subscription (subscriptions.save_subscription
    # sets it). Deliberately no onupdate: the worker's lease and last_sent_at
    # writes must not count as a refresh -- the device cap drops the least
    # recently refreshed device, not the one that was simply sent to least.
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PushOutbox(Base, UUIDPKMixin):
    __tablename__ = "owner_push_outbox"
    __table_args__ = (
        UniqueConstraint("event_type", "sales_order_id", name="uq_owner_push_outbox_event_sale"),
        CheckConstraint("status IN ('PENDING', 'EXPANDED', 'SKIPPED')", name="ck_owner_push_outbox_status"),
        Index("ix_owner_push_outbox_status_created", "status", "created_at"),
    )

    event_type: Mapped[str] = mapped_column(String(48), nullable=False)
    sales_order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_sales_orders.id"), nullable=False
    )
    payment_record_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_payment_records.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING")
    skip_reason: Mapped[str | None] = mapped_column(String(48))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PushDelivery(Base, UUIDPKMixin):
    __tablename__ = "owner_push_deliveries"
    __table_args__ = (
        UniqueConstraint("outbox_id", "subscription_id", name="uq_owner_push_deliveries_outbox_subscription"),
        CheckConstraint(
            "status IN ('QUEUED', 'SENT', 'FAILED_PERMANENT', 'CANCELLED')", name="ck_owner_push_deliveries_status"
        ),
        # A QUEUED row must still have a device to go to; a device that
        # unsubscribed is deleted only after its rows were finished.
        CheckConstraint("status <> 'QUEUED' OR subscription_id IS NOT NULL", name="ck_owner_push_deliveries_queued_has_device"),
        Index("ix_owner_push_deliveries_status_next_attempt", "status", "next_attempt_at"),
    )

    outbox_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_push_outbox.id"), nullable=False, index=True
    )
    # SET NULL, not CASCADE: when a device is removed (unsubscribed, 410, staff
    # deactivated) its finished rows stay as the record of what was sent.
    subscription_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_push_subscriptions.id", ondelete="SET NULL"), index=True
    )
    staff_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="QUEUED")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(48))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
