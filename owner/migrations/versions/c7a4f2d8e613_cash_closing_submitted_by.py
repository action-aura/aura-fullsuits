"""cash_closing_submitted_by (AUDIT-032, ported from the August master lineage)

Revision ID: c7a4f2d8e613
Revises: b5e1c8a27d94
Create Date: 2026-10-12 12:00:00.000000

Additive only: one nullable column, no backfill, existing rows untouched
(a closing submitted before this column existed simply has no recorded
submitter, so the widened self-approval check falls back to prepared_by
alone for it -- exactly today's behaviour). Safe to apply with the old code
still running (it never reads the column) and safe to roll back (downgrade
drops only this column).

submitted_by_staff_user_id records who actually supplied the counted-cash
figure via submit_closing(), distinct from prepared_by_staff_user_id (set once
at DRAFT creation and never reassigned). See app/models/cash_closing.py.

b5e1c8a27d94 was named as this wave's down_revision in ROADMAP.md before work
started (single-writer resource: exactly one new head on top of it).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = 'c7a4f2d8e613'
down_revision = 'b5e1c8a27d94'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "owner_cash_closings",
        sa.Column("submitted_by_staff_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("owner_staff_users.id")),
    )


def downgrade() -> None:
    op.drop_column("owner_cash_closings", "submitted_by_staff_user_id")
