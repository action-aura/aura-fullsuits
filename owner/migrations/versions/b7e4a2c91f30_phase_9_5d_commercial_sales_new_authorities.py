"""phase_9_5d_commercial_sales_new_authorities

Revision ID: b7e4a2c91f30
Revises: a1f9c3d76e02
Create Date: 2026-08-02 09:30:00.000000

Milestone 1's audit found the entire Quote/SalesOrder/CommercialInvoice/
CommercialRefund schema already migrated (Phase 9.5A) but three real
authorities genuinely missing: document numbering, a per-document
approval record, and payment-to-invoice allocation. This migration adds
exactly those three tables -- no existing table is altered, no existing
authority duplicated.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = 'b7e4a2c91f30'
down_revision = 'a1f9c3d76e02'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'owner_document_number_counters',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('document_type', sa.String(32), nullable=False),
        sa.Column('period_key', sa.String(8), nullable=False),
        sa.Column('next_value', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('document_type', 'period_key', name='uq_document_number_counter'),
    )

    op.create_table(
        'owner_commercial_approvals',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('target_type', sa.String(16), nullable=False),
        sa.Column('target_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('target_version_at_request', sa.Integer(), nullable=False),
        sa.Column('reason_code', sa.String(32), nullable=False),
        sa.Column('requested_by_staff_user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('owner_staff_users.id'), nullable=False),
        sa.Column('requested_values', postgresql.JSONB(), nullable=False),
        sa.Column('original_values', postgresql.JSONB(), nullable=False),
        sa.Column('status', sa.String(16), nullable=False, server_default='PENDING'),
        sa.Column('decided_by_staff_user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('owner_staff_users.id'), nullable=True),
        sa.Column('decision_reason', sa.Text(), nullable=True),
        sa.Column('requested_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_owner_commercial_approvals_target', 'owner_commercial_approvals', ['target_type', 'target_id'])

    op.create_table(
        'owner_payment_allocations',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('payment_record_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('owner_payment_records.id'), nullable=False),
        sa.Column('commercial_invoice_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('owner_commercial_invoices.id'), nullable=False),
        sa.Column('allocated_amount', sa.Numeric(12, 2), nullable=False),
        sa.Column('currency', sa.String(3), nullable=False),
        sa.Column('allocated_by_staff_user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('owner_staff_users.id'), nullable=False),
        sa.Column('allocated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('reversed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('reversed_by_staff_user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('owner_staff_users.id'), nullable=True),
        sa.Column('reversal_reason', sa.Text(), nullable=True),
        sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_owner_payment_allocations_payment_record_id', 'owner_payment_allocations', ['payment_record_id'])
    op.create_index('ix_owner_payment_allocations_commercial_invoice_id', 'owner_payment_allocations', ['commercial_invoice_id'])


def downgrade() -> None:
    op.drop_index('ix_owner_payment_allocations_commercial_invoice_id', table_name='owner_payment_allocations')
    op.drop_index('ix_owner_payment_allocations_payment_record_id', table_name='owner_payment_allocations')
    op.drop_table('owner_payment_allocations')

    op.drop_index('ix_owner_commercial_approvals_target', table_name='owner_commercial_approvals')
    op.drop_table('owner_commercial_approvals')

    op.drop_table('owner_document_number_counters')
