"""Customer website accounts (Step 2, slice CA1, revision a6e81bf3d32c).

A customer's account on the Action Aura website lives HERE, in Owner, tied to
the one customer record staff already work with -- the website is a thin
client (it calls the server-to-server API in app/customer_accounts/api.py).

An account is about signing in to the website. It is NOT a licence: disabling
or resetting an account never touches a licence, a lease or a device
(test_customer_accounts_service.py pins this).

State machine:
    INVITED --(set-password link used)--> ACTIVE
    INVITED/ACTIVE --(staff, with reason)--> DISABLED --(staff)--> ACTIVE or INVITED
An account only becomes ACTIVE through a set-password link mailed (or handed)
to the address, so ACTIVE also means "the address was verified".

Secrets: only SHA-256 hashes of tokens are stored. Login attempts store hashed
email / IP (a lockout key, not a person's address).
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPKMixin

ACCOUNT_STATUSES = ("INVITED", "ACTIVE", "DISABLED")
TOKEN_KINDS = ("INVITE", "RESET", "EMAIL_CHANGE")


class CustomerAccount(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_customer_accounts"
    __table_args__ = (
        CheckConstraint("status IN ('INVITED', 'ACTIVE', 'DISABLED')", name="ck_owner_customer_accounts_status"),
        CheckConstraint("language IN ('en', 'ar')", name="ck_owner_customer_accounts_language"),
        # ACTIVE means a password exists: the only way in is a set-password link.
        CheckConstraint("status <> 'ACTIVE' OR password_hash IS NOT NULL", name="ck_owner_customer_accounts_active_has_password"),
        CheckConstraint(
            "(status = 'DISABLED' AND disabled_at IS NOT NULL) OR (status <> 'DISABLED' AND disabled_at IS NULL)",
            name="ck_owner_customer_accounts_disabled_at",
        ),
    )

    # One person per customer for now (unique index). A second person later
    # means dropping this one index -- nothing else assumes it.
    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_customers.id"), nullable=False, unique=True
    )
    # Stored lowercased and trimmed. ONE EMAIL = ONE COMPANY: unique across all customers.
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    password_hash: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(16), default="INVITED", nullable=False)
    language: Mapped[str] = mapped_column(String(8), default="en", nullable=False)
    # Bumped on every password change / reset / "sign out everywhere": every
    # session carries the value it was created under and dies when it differs.
    session_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_sign_in_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    disabled_reason: Mapped[str | None] = mapped_column(Text)
    disabled_by_staff_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id")
    )
    created_by_staff_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id")
    )


class CustomerAccountToken(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_customer_account_tokens"
    __table_args__ = (
        CheckConstraint("kind IN ('INVITE', 'RESET', 'EMAIL_CHANGE')", name="ck_owner_customer_account_tokens_kind"),
        CheckConstraint(
            "(kind = 'EMAIL_CHANGE' AND new_email IS NOT NULL) OR (kind <> 'EMAIL_CHANGE' AND new_email IS NULL)",
            name="ck_owner_customer_account_tokens_new_email",
        ),
    )

    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_customer_accounts.id"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    new_email: Mapped[str | None] = mapped_column(String(255))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_staff_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id")
    )


class CustomerSession(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_customer_sessions"

    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_customer_accounts.id"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    session_version_at_login: Mapped[int] = mapped_column(Integer, nullable=False)
    ip_address: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(512))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    absolute_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_reason: Mapped[str | None] = mapped_column(String(64))


class CustomerLoginAttempt(Base, UUIDPKMixin, TimestampMixin):
    """Telemetry for customer sign-ins. A different table from the staff
    owner_login_attempts on purpose: a customer hammering the website must never
    be able to lock out, or fill the history of, a staff account."""

    __tablename__ = "owner_customer_login_attempts"

    email_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    ip_hash: Mapped[str | None] = mapped_column(String(64))
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    reason: Mapped[str | None] = mapped_column(String(64))
