"""registry_device_id reservation survives terminal status + history index (AUDIT-033, review V3o G1/G2/G3)

Revision ID: 7c41d2e9a0b5
Revises: 5f2c8e1a9b37
Create Date: 2026-10-02 14:00:00.000000

Review V3o found that Owner's pin was only reserved while the holder could
still speak: a REPLACED/DEACTIVATED holder freed the id, so an insider's
installation that was already lurking could claim the main's id the moment
staff replaced the main's slot (G2). The reservation is now permanent: the
unique index drops its status predicate, so (license_id, registry_device_id)
is unique across EVERY status. An id moves only by an explicit, audited staff
action (registry_pin.transfer_registry_device_id).

Also adds ix_owner_sync_events_license_entity so the history guard (G3: "was
this id already in the shop's synced history before this installation
existed?") is an index lookup, not a scan of the whole event log.

Additive/tightening only. Upgrade fails loudly (IntegrityError) if existing
rows already share an id across statuses; that is a real conflict staff must
resolve, not something to paper over here.
"""
from alembic import op
import sqlalchemy as sa


revision = '7c41d2e9a0b5'
down_revision = '5f2c8e1a9b37'
branch_labels = None
depends_on = None

_OLD_PRED = "registry_device_id IS NOT NULL AND status NOT IN ('DEACTIVATED','REPLACED')"
_NEW_PRED = "registry_device_id IS NOT NULL"
_IDX = "uq_owner_installations_license_registry_device"


def upgrade() -> None:
    op.drop_index(_IDX, table_name="owner_installations")
    op.create_index(
        _IDX, "owner_installations", ["license_id", "registry_device_id"], unique=True,
        postgresql_where=sa.text(_NEW_PRED),
    )
    op.create_index("ix_owner_sync_events_license_entity", "owner_sync_events", ["license_id", "entity_type", "entity_id"])


def downgrade() -> None:
    op.drop_index("ix_owner_sync_events_license_entity", table_name="owner_sync_events")
    op.drop_index(_IDX, table_name="owner_installations")
    op.create_index(
        _IDX, "owner_installations", ["license_id", "registry_device_id"], unique=True,
        postgresql_where=sa.text(_OLD_PRED),
    )
