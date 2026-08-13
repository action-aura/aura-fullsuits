"""phase_9_5c_crm_ownership_history_additions

Revision ID: 911ac2a05c12
Revises: 338d06dece44
Create Date: 2026-08-01 20:07:19.515056

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = '911ac2a05c12'
down_revision = '338d06dece44'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('owner_lead_assignments', sa.Column('reason', sa.Text(), nullable=True))

    # server_default backfills every pre-existing row, then is dropped so
    # the DB schema matches the model exactly (models declare a Python-side
    # ORM default only, not a server_default -- matching every other
    # `version`-style column added by the original Phase 9.5A migration).
    # Without dropping it, test_no_schema_drift_between_models_and_migration
    # would (correctly) flag a permanent mismatch.
    op.add_column(
        'owner_lead_notes',
        sa.Column('visibility', sa.String(length=32), nullable=False, server_default='ASSIGNED_RECORD_USERS'),
    )
    op.alter_column('owner_lead_notes', 'visibility', server_default=None)
    op.add_column('owner_lead_notes', sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True))

    op.add_column(
        'owner_customer_notes',
        sa.Column('visibility', sa.String(length=32), nullable=False, server_default='ASSIGNED_RECORD_USERS'),
    )
    op.alter_column('owner_customer_notes', 'visibility', server_default=None)
    op.add_column('owner_customer_notes', sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True))

    op.add_column('owner_customers', sa.Column('version', sa.Integer(), nullable=False, server_default='1'))
    op.alter_column('owner_customers', 'version', server_default=None)

    op.create_table(
        'owner_customer_assignments',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('customer_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('owner_customers.id'), nullable=False),
        sa.Column('assigned_to_staff_user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('owner_staff_users.id'), nullable=False),
        sa.Column('assigned_by_staff_user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('owner_staff_users.id'), nullable=False),
        sa.Column('assigned_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('unassigned_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
    )
    op.create_index('ix_owner_customer_assignments_customer_id', 'owner_customer_assignments', ['customer_id'])


def downgrade() -> None:
    op.drop_index('ix_owner_customer_assignments_customer_id', table_name='owner_customer_assignments')
    op.drop_table('owner_customer_assignments')
    op.drop_column('owner_customers', 'version')
    op.drop_column('owner_customer_notes', 'archived_at')
    op.drop_column('owner_customer_notes', 'visibility')
    op.drop_column('owner_lead_notes', 'archived_at')
    op.drop_column('owner_lead_notes', 'visibility')
    op.drop_column('owner_lead_assignments', 'reason')
