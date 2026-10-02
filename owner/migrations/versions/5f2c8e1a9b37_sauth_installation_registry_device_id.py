"""installation registry_device_id pin (AUDIT-033 S-AUTH fix round)

Revision ID: 5f2c8e1a9b37
Revises: 54627d59dd59
Create Date: 2026-10-02 09:00:00.000000

Verifier V finding #1 (CRITICAL): receivers bound a signer's registry device
id by trust-on-first-use, so an insider could bind the main's id to its own
installation first. Owner now pins the id per installation (first claim wins,
immutable) and signs it into every lease as `registry_device_id`; receivers
accept an id only when the signer's Owner lease attests it.

The unique index is partial: a pin is unique per licence among installations
that can still speak. DEACTIVATED/REPLACED installations free the id so a
re-activated or slot-replaced computer can pin its own id again. Both columns
are nullable: every existing installation starts unpinned and pins on its
next check-in. Additive only; nothing is rewritten.
"""
from alembic import op
import sqlalchemy as sa


revision = '5f2c8e1a9b37'
down_revision = '54627d59dd59'
branch_labels = None
depends_on = None

_PRED = "registry_device_id IS NOT NULL AND status NOT IN ('DEACTIVATED','REPLACED')"


def upgrade() -> None:
    op.add_column("owner_installations", sa.Column("registry_device_id", sa.String(64), nullable=True))
    op.add_column("owner_installations", sa.Column("registry_device_id_pinned_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(
        "uq_owner_installations_license_registry_device", "owner_installations",
        ["license_id", "registry_device_id"], unique=True, postgresql_where=sa.text(_PRED),
    )


def downgrade() -> None:
    op.drop_index("uq_owner_installations_license_registry_device", table_name="owner_installations")
    op.drop_column("owner_installations", "registry_device_id_pinned_at")
    op.drop_column("owner_installations", "registry_device_id")