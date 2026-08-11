"""phase_9_5c_contacts

Revision ID: 870b08809d22
Revises: fe581c2bb967
Create Date: 2026-08-01 22:26:10.244525

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = '870b08809d22'
down_revision = 'fe581c2bb967'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('owner_customer_contacts', sa.Column('notes', sa.Text(), nullable=True))
    op.add_column('owner_customer_contacts', sa.Column('version', sa.Integer(), nullable=False, server_default='1'))
    op.alter_column('owner_customer_contacts', 'version', server_default=None)
    op.add_column('owner_customer_contacts', sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        'owner_lead_contacts',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('lead_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('owner_leads.id'), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('title', sa.String(length=128), nullable=True),
        sa.Column('business_email', sa.String(length=255), nullable=True),
        sa.Column('business_phone', sa.String(length=64), nullable=True),
        sa.Column('preferred_channel', sa.String(length=32), nullable=True),
        sa.Column('is_primary', sa.Boolean(), nullable=False),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
    )
    op.create_index('ix_owner_lead_contacts_lead_id', 'owner_lead_contacts', ['lead_id'])


def downgrade() -> None:
    op.drop_index('ix_owner_lead_contacts_lead_id', table_name='owner_lead_contacts')
    op.drop_table('owner_lead_contacts')
    op.drop_column('owner_customer_contacts', 'archived_at')
    op.drop_column('owner_customer_contacts', 'version')
    op.drop_column('owner_customer_contacts', 'notes')
