"""Phase 9R M11 -- private authorized release distribution.

A ReleaseDownloadAuthorization is a short-lived, single-use, server-issued
credential for fetching exactly one private release artifact. Never a
public/permanent URL -- the token is the only way to fetch, it expires,
and it is consumed on first successful use. Mirrors the existing
invitation-token pattern (app/security/tokens.py): the raw token is
returned to the caller once, only its hash is ever stored.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPKMixin


class ReleaseDownloadAuthorization(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "owner_release_download_authorizations"

    product_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_product_versions.id"), nullable=False
    )
    license_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("owner_licenses.id"), nullable=False)
    installation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owner_installations.id"), nullable=False
    )
    # SHA-256 of the raw token -- the raw value is returned to the caller
    # exactly once (the authorize-download response) and never stored.
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    max_uses: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    use_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    product_version = relationship("ProductVersion")
    license = relationship("License")
    installation = relationship("Installation")
