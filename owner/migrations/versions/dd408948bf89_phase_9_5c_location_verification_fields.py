"""phase_9_5c_location_verification_fields

Revision ID: dd408948bf89
Revises: 870b08809d22
Create Date: 2026-08-01 22:35:50.459226

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = 'dd408948bf89'
down_revision = '870b08809d22'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'owner_customer_locations',
        sa.Column('verified_by_employee_profile_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('owner_employee_profiles.id'), nullable=True),
    )
    op.add_column('owner_customer_locations', sa.Column('verified_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('owner_customer_locations', sa.Column('verification_reason', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('owner_customer_locations', 'verification_reason')
    op.drop_column('owner_customer_locations', 'verified_at')
    op.drop_column('owner_customer_locations', 'verified_by_employee_profile_id')
