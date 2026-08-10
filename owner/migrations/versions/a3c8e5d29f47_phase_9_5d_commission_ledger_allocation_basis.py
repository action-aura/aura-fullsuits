"""phase_9_5d_commission_ledger_allocation_basis

Revision ID: a3c8e5d29f47
Revises: f2b7d4e91a63
Create Date: 2026-08-02 13:15:00.000000

Real gap found starting Milestone 15: owner_commission_ledger_entries'
uniqueness constraint originally deduped on source_payment_record_id
(Phase 9.5A, before PaymentAllocation existed). Non-Negotiable Rule:
commission basis is confirmed Payment Allocation, and partial
allocations create proportional earnings -- Milestone 11 already proved
a single Payment can be split-allocated across multiple Invoices, each
needing its own commission. A per-payment uniqueness constraint would
silently block the second allocation's legitimate earning. Adds
source_payment_allocation_id and moves the uniqueness constraint onto
it. Table was empty (MODEL PRESENT SERVICE MISSING per Milestone 1's
audit) -- no backfill needed, added NOT NULL directly.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = 'a3c8e5d29f47'
down_revision = 'f2b7d4e91a63'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index('uq_commission_ledger_one_entry_per_payment', table_name='owner_commission_ledger_entries')
    op.add_column(
        'owner_commission_ledger_entries',
        sa.Column('source_payment_allocation_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('owner_payment_allocations.id'), nullable=False),
    )
    op.create_index('ix_owner_commission_ledger_entries_source_payment_allocation_id', 'owner_commission_ledger_entries', ['source_payment_allocation_id'])
    op.create_index(
        'uq_commission_ledger_one_entry_per_allocation',
        'owner_commission_ledger_entries',
        ['source_payment_allocation_id'],
        unique=True,
        postgresql_where=sa.text('reversal_of_ledger_entry_id IS NULL'),
    )


def downgrade() -> None:
    op.drop_index('uq_commission_ledger_one_entry_per_allocation', table_name='owner_commission_ledger_entries')
    op.drop_index('ix_owner_commission_ledger_entries_source_payment_allocation_id', table_name='owner_commission_ledger_entries')
    op.drop_column('owner_commission_ledger_entries', 'source_payment_allocation_id')
    op.create_index(
        'uq_commission_ledger_one_entry_per_payment',
        'owner_commission_ledger_entries',
        ['source_payment_record_id'],
        unique=True,
        postgresql_where=sa.text('reversal_of_ledger_entry_id IS NULL'),
    )
