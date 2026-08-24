"""sync_device_cursors table

Revision ID: 15bfb9e618a3
Revises: d8dfeb46d1d6
Create Date: 2026-08-24 09:00:00.000000

Phase 5 prerequisite #2 (docs/launch-readiness/phase5-prerequisites.md
section 2, ROADMAP.md's "Two things Phase 5 must not open the firehose
without") -- durable, server-side record of each device's own claim of
"how far it has caught up" via GET/POST /api/sync/v1/pull's `since`/
returned-`cursor` value. Nothing in the sync protocol persisted this
before: `since` was a stateless request parameter the client alone
tracked. Pruning owner_sync_events below the slowest ACTIVE device's
cursor needs exactly this fact, durably, per device, per license --
see app/sync/pruning.py.

`installation_id` is the primary key (one row per device -- device ids
are already globally unique, no license-scoping ambiguity). `license_id`
is denormalized onto the row so pruning's per-license MIN(last_acked_seq)
query is a single indexed lookup rather than a join back through
owner_installations on every run. Only ever written by pull()
(app/sync/routes.py::_advance_device_cursor) -- push() never touches this
table (see SyncDeviceCursor's own docstring in app/models/sync.py for why).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = '15bfb9e618a3'
down_revision = 'd8dfeb46d1d6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "owner_sync_device_cursors",
        sa.Column("installation_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("owner_installations.id"), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("license_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("owner_licenses.id"), nullable=False),
        sa.Column("last_acked_seq", sa.BigInteger(), nullable=False),
    )
    op.create_index("ix_owner_sync_device_cursors_license", "owner_sync_device_cursors", ["license_id"])


def downgrade() -> None:
    op.drop_index("ix_owner_sync_device_cursors_license", table_name="owner_sync_device_cursors")
    op.drop_table("owner_sync_device_cursors")
