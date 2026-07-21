"""Staff, RBAC, session, invitation, and MFA models (Part D: STAFF AND SECURITY)."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPKMixin


class Role(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_roles"

    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    is_system_role: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    permissions: Mapped[list["RolePermission"]] = relationship(back_populates="role", cascade="all, delete-orphan")


class Permission(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_permissions"

    code: Mapped[str] = mapped_column(String(96), unique=True, nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)


class RolePermission(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_role_permissions"
    __table_args__ = (UniqueConstraint("role_id", "permission_id", name="uq_role_permission"),)

    role_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_roles.id"), nullable=False)
    permission_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_permissions.id"), nullable=False
    )

    role: Mapped[Role] = relationship(back_populates="permissions")
    permission: Mapped[Permission] = relationship()


class StaffUser(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_staff_users"

    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_super_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    mfa_required: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    session_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    disabled_reason: Mapped[str | None] = mapped_column(Text)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    role_assignments: Mapped[list["StaffRoleAssignment"]] = relationship(
        back_populates="staff_user",
        cascade="all, delete-orphan",
        foreign_keys="StaffRoleAssignment.staff_user_id",
    )
    mfa_credential: Mapped["MfaCredential | None"] = relationship(
        back_populates="staff_user", uselist=False, cascade="all, delete-orphan"
    )


class StaffRoleAssignment(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_staff_role_assignments"
    __table_args__ = (UniqueConstraint("staff_user_id", "role_id", name="uq_staff_role"),)

    staff_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id"), nullable=False
    )
    role_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_roles.id"), nullable=False)
    assigned_by_staff_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id")
    )

    staff_user: Mapped[StaffUser] = relationship(back_populates="role_assignments", foreign_keys=[staff_user_id])
    role: Mapped[Role] = relationship()


class StaffSession(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_staff_sessions"

    staff_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    session_version_at_login: Mapped[int] = mapped_column(Integer, nullable=False)
    ip_address: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(512))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    mfa_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_reason: Mapped[str | None] = mapped_column(Text)


class StaffInvitation(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_staff_invitations"

    email: Mapped[str] = mapped_column(String(255), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    role_codes: Mapped[str] = mapped_column(Text, nullable=False)  # comma-separated role codes, fixed at creation
    invited_by_staff_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id"), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_staff_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id")
    )


class MfaCredential(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_mfa_credentials"

    staff_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id"), unique=True, nullable=False
    )
    totp_secret_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    enrolled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    staff_user: Mapped[StaffUser] = relationship(back_populates="mfa_credential")
    recovery_codes: Mapped[list["MfaRecoveryCode"]] = relationship(
        back_populates="mfa_credential", cascade="all, delete-orphan"
    )


class MfaRecoveryCode(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_mfa_recovery_codes"

    mfa_credential_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_mfa_credentials.id"), nullable=False
    )
    code_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    mfa_credential: Mapped[MfaCredential] = relationship(back_populates="recovery_codes")


class LoginAttempt(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_login_attempts"

    email_attempted: Mapped[str] = mapped_column(String(255), nullable=False)
    ip_address: Mapped[str | None] = mapped_column(String(64))
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    reason: Mapped[str | None] = mapped_column(String(128))
