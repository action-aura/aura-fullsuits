"""Personal-data requests (Alembic DR1): a customer asks us for a copy of what we hold about
them (EXPORT) or to delete it (ERASE). The law gives us a month to answer, so each request is
logged the day it arrives with its due date, and closed with a written answer.

ONE TABLE. The row holds NO personal data of the customer: it points at the customer by id and
carries the staff's words (the answer note). It outlives the erasure it records -- the proof that
we answered, and when, is exactly what must survive an erasure.

States: OPEN -> DONE | REFUSED. A closed request always carries an answer note and a closing time
(CHECKs below). An ERASE can only be DONE after the erasure really ran: `fulfilled_at` is stamped
by the erasure itself (privacy_requests/erase.py), and the database refuses a DONE ERASE without it
-- so no code path, present or future, can close an erase request "done" on nothing.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Identity, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPKMixin

REQUEST_KINDS = ("EXPORT", "ERASE")
REQUEST_CHANNELS = ("EMAIL", "PHONE", "IN_PERSON")
REQUEST_STATUSES = ("OPEN", "DONE", "REFUSED")
# The law's month. A request is "overdue" once its due time has passed and it is still OPEN.
RESPONSE_DAYS = 30


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


class DataRequest(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_data_requests"
    __table_args__ = (
        CheckConstraint(_in("kind", REQUEST_KINDS), name="ck_owner_data_requests_kind"),
        CheckConstraint(_in("channel", REQUEST_CHANNELS), name="ck_owner_data_requests_channel"),
        CheckConstraint(_in("status", REQUEST_STATUSES), name="ck_owner_data_requests_status"),
        CheckConstraint("due_at >= received_at", name="ck_owner_data_requests_due_after_received"),
        # Open = not closed; closed = has a closing time.
        CheckConstraint("(status = 'OPEN') = (closed_at IS NULL)", name="ck_owner_data_requests_closed_time"),
        # A closed request says what was answered (or why it was refused).
        CheckConstraint(
            "status = 'OPEN' OR length(btrim(coalesce(answer_note, ''))) > 0", name="ck_owner_data_requests_answer_note"),
        # An ERASE is DONE only after the erasure ran (fulfilled_at is stamped by it).
        CheckConstraint(
            "NOT (kind = 'ERASE' AND status = 'DONE') OR fulfilled_at IS NOT NULL", name="ck_owner_data_requests_erase_done"),
        Index("ix_owner_data_requests_status_due", "status", "due_at"),
    )

    # The number people say out loud. Never used in a URL: the id is.
    request_number: Mapped[int] = mapped_column(Integer, Identity(always=False), nullable=False, unique=True)
    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_customers.id"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)
    channel: Mapped[str] = mapped_column(String(12), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(8), nullable=False, default="OPEN", server_default="OPEN")
    answer_note: Mapped[str | None] = mapped_column(Text)
    logged_by_staff_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id"), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_by_staff_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id"))
    # ERASE: when the erasure ran. EXPORT: when the copy was first handed over (downloaded).
    fulfilled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
