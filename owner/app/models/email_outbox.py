"""Owner's outbound email queue.

One row per message Owner has to send. Two producers write here:

  * source='owner'  -- Owner's own mail (today: staff invitations).
  * source='relay'  -- a product install asked Owner to send a message on its
    behalf (app/mail/relay.py). The product never holds the mailbox password;
    only this server does.

The worker (app/mail/worker.py) is the only thing that talks SMTP. The state
machine is the same as commercial_runtime/notifications/outbox.py's (QUEUED ->
SENDING -> SENT, retry back to QUEUED with backoff, FAILED_PERMANENT after the
last attempt, CANCELLED by an operator) so the two queues behave alike.

`last_error` holds an exception CLASS NAME only ("SMTPAuthenticationError"),
never a message: an SMTP error text can echo the recipient or the login.
`installation_id` is what the per-installation caps count by, and is NULL for
Owner's own mail.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPKMixin

STATUSES = ("QUEUED", "SENDING", "SENT", "FAILED_PERMANENT", "CANCELLED")
SOURCES = ("owner", "relay")


class EmailOutbox(Base, UUIDPKMixin):
    __tablename__ = "owner_email_outbox"
    __table_args__ = (
        # The worker's claim query: due QUEUED rows, oldest first.
        Index("ix_owner_email_outbox_status_next_attempt", "status", "next_attempt_at"),
        # The relay caps: how many messages did this installation queue lately.
        Index("ix_owner_email_outbox_installation_created", "installation_id", "created_at"),
        # One message per EVENT for the notices (a contract acceptance, a payment status):
        # the key names the event ("customer_contract_accepted:<acceptance id>"), the
        # database refuses a second row for it, so a retried request or a re-run job
        # cannot mail the same person twice. NULL (every older kind) is not constrained.
        Index("uq_owner_email_outbox_dedupe_key", "dedupe_key", unique=True, postgresql_where=text("dedupe_key IS NOT NULL")),
    )

    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    to_address: Mapped[str] = mapped_column(String(320), nullable=False)
    subject: Mapped[str] = mapped_column(String(300), nullable=False)
    body_text: Mapped[str] = mapped_column(Text, nullable=False)
    body_html: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="QUEUED")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(120))
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="owner")
    installation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_installations.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dedupe_key: Mapped[str | None] = mapped_column(String(200))
