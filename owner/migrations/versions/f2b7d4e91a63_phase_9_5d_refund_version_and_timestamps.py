"""phase_9_5d_refund_version_and_timestamps

Revision ID: f2b7d4e91a63
Revises: e4a8c1f6b0d3
Create Date: 2026-08-02 12:00:00.000000

Real gap found while starting Milestone 12: CommercialRefund had no
optimistic-lock version column and no per-transition timestamps
(approved_at/paid_at/voided_at), unlike every sibling document
(Quote/SalesOrder/CommercialInvoice) which has both. Additive columns
plus indexes on commercial_invoice_id/payment_record_id (applying the
lesson from Phase 9.5C's own Milestone 24 -- add indexes proactively
where a FK is a real query predicate, not after finding the gap via a
100k-row EXPLAIN ANALYZE pass).
"""
from alembic import op
import sqlalchemy as sa


revision = 'f2b7d4e91a63'
down_revision = 'e4a8c1f6b0d3'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('owner_commercial_refunds', sa.Column('version', sa.Integer(), nullable=False, server_default='1'))
    op.alter_column('owner_commercial_refunds', 'version', server_default=None)
    op.add_column('owner_commercial_refunds', sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('owner_commercial_refunds', sa.Column('paid_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('owner_commercial_refunds', sa.Column('voided_at', sa.DateTime(timezone=True), nullable=True))
    op.create_index('ix_owner_commercial_refunds_commercial_invoice_id', 'owner_commercial_refunds', ['commercial_invoice_id'])
    op.create_index('ix_owner_commercial_refunds_payment_record_id', 'owner_commercial_refunds', ['payment_record_id'])


def downgrade() -> None:
    op.drop_index('ix_owner_commercial_refunds_payment_record_id', table_name='owner_commercial_refunds')
    op.drop_index('ix_owner_commercial_refunds_commercial_invoice_id', table_name='owner_commercial_refunds')
    op.drop_column('owner_commercial_refunds', 'voided_at')
    op.drop_column('owner_commercial_refunds', 'paid_at')
    op.drop_column('owner_commercial_refunds', 'approved_at')
    op.drop_column('owner_commercial_refunds', 'version')
