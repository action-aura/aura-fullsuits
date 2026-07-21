"""Phase 6 -- Licensing & Activation Service models.

New tables only; no Phase 5 table is altered by this module (see
docs/owner/phase6/phase5-to-phase6-migration-report.md). None of these tables
ever stores a plaintext license key, a device private key, or a server
signing private key -- private key material lives on disk under
OWNER_SIGNING_KEY_DIRECTORY (server keys) or the simulator's local device
directory (device keys), never in a database column.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPKMixin


class SigningKey(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_signing_keys"

    key_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    algorithm: Mapped[str] = mapped_column(String(32), default="ed25519", nullable=False)
    public_key: Mapped[str] = mapped_column(Text, nullable=False)  # base64 raw public key -- safe to store/publish
    status: Mapped[str] = mapped_column(String(16), default="DRAFT", nullable=False)  # DRAFT/ACTIVE/RETIRED/REVOKED
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revocation_reason: Mapped[str | None] = mapped_column(Text)


class DevicePublicKey(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_device_public_keys"

    installation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_installations.id"), nullable=False
    )
    public_key: Mapped[str] = mapped_column(Text, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)  # sha256 hex of raw public key
    algorithm: Mapped[str] = mapped_column(String(32), default="ed25519", nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE", nullable=False)  # ACTIVE/REVOKED/REPLACED
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    replaced_by_device_key_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_device_public_keys.id")
    )
    last_proof_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    installation: Mapped["Installation"] = relationship()  # noqa: F821


class ActivationRequest(Base, UUIDPKMixin, TimestampMixin):
    """A lightweight structured record of every external request this service
    decided on -- distinct from owner_audit_log (which remains the append-only,
    hash-chained authority). This table exists to support the admin 'recent
    activation requests' view and idempotency-conflict diagnostics without
    scanning the audit log's much broader event stream."""

    __tablename__ = "owner_activation_requests"

    request_id: Mapped[str] = mapped_column(String(128), nullable=False)  # client-supplied, not globally unique alone
    correlation_id: Mapped[str | None] = mapped_column(String(128))
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)  # ACTIVATION / CHECK_IN / DEACTIVATION
    product_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_products.id"))
    platform_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_platforms.id"))
    license_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_licenses.id"))
    installation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_installations.id"))
    device_key_fingerprint: Mapped[str | None] = mapped_column(String(64))
    source_ip: Mapped[str | None] = mapped_column(String(64))
    result: Mapped[str] = mapped_column(String(16), nullable=False)  # ACCEPTED / REJECTED
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)


class SignedAssertion(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_signed_assertions"

    assertion_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    installation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_installations.id"), nullable=False
    )
    license_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_licenses.id"), nullable=False)
    signing_key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    assertion_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    not_before: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    installation: Mapped["Installation"] = relationship()  # noqa: F821
    license: Mapped["License"] = relationship()  # noqa: F821
    entitlement_snapshot: Mapped["EntitlementSnapshot | None"] = relationship(
        back_populates="assertion", uselist=False
    )


class EntitlementSnapshot(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_entitlement_snapshots"

    assertion_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_signed_assertions.id"), unique=True, nullable=False
    )
    entitlements: Mapped[dict] = mapped_column(JSONB, nullable=False)  # {code: {value, value_type}} -- no _source leak
    resolved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    assertion: Mapped[SignedAssertion] = relationship(back_populates="entitlement_snapshot")


class ExternalIdempotencyRecord(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_external_idempotency_records"
    __table_args__ = (UniqueConstraint("idempotency_key_hash", "operation_type", name="uq_idempotency_key_operation"),)

    idempotency_key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    operation_type: Mapped[str] = mapped_column(String(32), nullable=False)  # ACTIVATION / DEACTIVATION
    request_fingerprint_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    result_reference: Mapped[str | None] = mapped_column(String(64))  # installation public id, when applicable
    status: Mapped[str] = mapped_column(String(16), default="COMPLETED", nullable=False)
    response_status: Mapped[str | None] = mapped_column(String(32))
    # The exact original response envelope (safe -- allowlisted fields only,
    # never a secret), replayed verbatim on an idempotent retry so the client
    # gets byte-identical proof it's the same logical result, not a new
    # re-signed assertion with a different signature every retry.
    cached_response_json: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class OfflinePolicy(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_offline_policies"

    policy_code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    policy_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    check_in_interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    retry_interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    offline_grace_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    warning_start_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    hard_expiry_behavior: Mapped[str] = mapped_column(String(64), default="WARN_ONLY", nullable=False)
    clock_rollback_tolerance_seconds: Mapped[int] = mapped_column(Integer, default=300, nullable=False)
    assertion_refresh_threshold_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    emergency_extension_allowed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    emergency_extension_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LicenseOfflinePolicyAssignment(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_license_offline_policy_assignments"

    license_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_licenses.id"), unique=True, nullable=False
    )
    offline_policy_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_offline_policies.id"), nullable=False
    )
    assigned_by_staff_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id")
    )

    license: Mapped["License"] = relationship()  # noqa: F821
    offline_policy: Mapped[OfflinePolicy] = relationship()


class SecurityNonceRecord(Base, UUIDPKMixin, TimestampMixin):
    """PostgreSQL-backed nonce store (ADR-6.3, chosen over Redis). A nonce is
    'used' the instant its row exists -- the unique constraint IS the atomic
    replay check (INSERT ... ON CONFLICT DO NOTHING, zero rows = replay)."""

    __tablename__ = "owner_security_nonce_records"
    __table_args__ = (UniqueConstraint("nonce", "scope", name="uq_nonce_scope"),)

    nonce: Mapped[str] = mapped_column(String(64), nullable=False)
    scope: Mapped[str] = mapped_column(String(32), nullable=False)  # e.g. installation_id or "activation"
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class KeyRotationEvent(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_key_rotation_events"

    from_key_id: Mapped[str | None] = mapped_column(String(64))
    to_key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    rotated_by_staff_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_staff_users.id")
    )
    reason: Mapped[str | None] = mapped_column(Text)


class RateLimitCounter(Base, UUIDPKMixin, TimestampMixin):
    """PostgreSQL-backed distributed rate-limit counter (ADR-6.3). bucket_key
    is a safe, non-secret derived identifier -- never a plaintext license key
    (Part M's explicit prohibition)."""

    __tablename__ = "owner_rate_limit_counters"
    __table_args__ = (UniqueConstraint("bucket_key", "window_start", name="uq_ratelimit_bucket_window"),)

    bucket_key: Mapped[str] = mapped_column(String(128), nullable=False)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class ServiceHealthEvent(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_service_health_events"

    check_type: Mapped[str] = mapped_column(String(32), nullable=False)  # DATABASE / REPLAY_STORE / SIGNING_KEY
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # OK / DEGRADED / FAILED
    detail: Mapped[str | None] = mapped_column(Text)
