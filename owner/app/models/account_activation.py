"""New customer flow, slice S5 (Alembic CA3, b1d4f8a26c93).

owner_customer_activation_grants holds the two short-lived "permission slips" a
device can present at POST /api/licensing/v1/activations INSTEAD of a licence key:

  ACCOUNT_TICKET   the customer signed in on THIS device with the email and password
                   of their Aura account (POST /account-sign-ins). Bound to the
                   device-key fingerprint that signed the sign-in, single use,
                   10 minutes, and dead the moment the account's password changes
                   (session_version_at_issue no longer matches the account's).
  ACTIVATION_CODE  support or a super admin pressed "Make activation code" for ONE
                   licence and read the code out to the customer. Single use,
                   bound to that licence, 72 hours.

Only a hash is stored. The ticket is a random 256-bit token (SHA-256); the code is
short enough to type, so it is stored as an HMAC under the licence pepper (a stolen
database does not let anyone brute-force the 60-bit space offline).

No password and no licence key is ever stored here.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPKMixin

GRANT_KINDS = ("ACCOUNT_TICKET", "ACTIVATION_CODE")


class CustomerActivationGrant(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_customer_activation_grants"
    __table_args__ = (
        CheckConstraint("kind IN ('ACCOUNT_TICKET', 'ACTIVATION_CODE')", name="ck_owner_customer_activation_grants_kind"),
        # The two kinds carry different facts; this makes a half-filled row of
        # either kind impossible to insert, whatever the application does.
        CheckConstraint(
            "(kind = 'ACCOUNT_TICKET' AND account_id IS NOT NULL AND device_key_fingerprint IS NOT NULL "
            "AND session_version_at_issue IS NOT NULL AND license_id IS NULL) OR "
            "(kind = 'ACTIVATION_CODE' AND license_id IS NOT NULL AND account_id IS NULL "
            "AND device_key_fingerprint IS NULL AND session_version_at_issue IS NULL)",
            name="ck_owner_customer_activation_grants_shape",
        ),
    )

    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_customers.id"), nullable=False, index=True
    )
    account_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_customer_accounts.id"))
    license_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_licenses.id"))
    # sha256 hex of the raw device public key that signed the sign-in (same value
    # as owner_device_public_keys.fingerprint will hold once the device activates).
    device_key_fingerprint: Mapped[str | None] = mapped_column(String(64))
    # The account's session_version when the ticket was made. Every password
    # change or reset bumps the account's value, so a ticket made before it stops
    # matching: that is how "a password reset kills the ticket" is enforced.
    session_version_at_issue: Mapped[int | None] = mapped_column(Integer)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_staff_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id")
    )
