"""phase_9_5d_quote_line_version

Revision ID: c92d5f18a4e6
Revises: b7e4a2c91f30
Create Date: 2026-08-02 10:15:00.000000

Real bug found while building Milestone 6's approval integration:
CommercialApproval.target_version_at_request was pinned to the parent
Quote's version, but Quote.version increments on unrelated actions
(submit, sibling line adds) that must never invalidate a still-accurate
pending approval for one specific line. Fix: QuoteLine gets its own
version column, matching the existing per-child-row optimistic-lock
pattern (LeadContact.version, CustomerContact.version,
CustomerLocation.version).
"""
from alembic import op
import sqlalchemy as sa


revision = 'c92d5f18a4e6'
down_revision = 'b7e4a2c91f30'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('owner_quote_lines', sa.Column('version', sa.Integer(), nullable=False, server_default='1'))
    op.alter_column('owner_quote_lines', 'version', server_default=None)


def downgrade() -> None:
    op.drop_column('owner_quote_lines', 'version')
