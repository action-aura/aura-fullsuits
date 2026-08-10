"""phase_9_5d_approval_commercial_fingerprint

Revision ID: e4a8c1f6b0d3
Revises: d15e6a3b7c92
Create Date: 2026-08-02 11:20:00.000000

Before treating Milestone 6 as closed: QuoteLine.version alone is only
a valid staleness signal if every approval-relevant mutation reliably
bumps it -- true today only because no line-edit function exists yet,
not because the mechanism is actually robust against future material
changes. Adds a deterministic commercial-fingerprint column, computed
from the actual commercial values (plan/addon, quantity, unit price,
override price, discount, currency) rather than an indirect version
counter. No existing rows (owner_commercial_approvals introduced this
phase, currently empty) -- added NOT NULL directly, no backfill dance
needed.
"""
from alembic import op
import sqlalchemy as sa


revision = 'e4a8c1f6b0d3'
down_revision = 'd15e6a3b7c92'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('owner_commercial_approvals', sa.Column('commercial_fingerprint', sa.String(64), nullable=False))


def downgrade() -> None:
    op.drop_column('owner_commercial_approvals', 'commercial_fingerprint')
