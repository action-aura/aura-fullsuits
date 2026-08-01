"""phase_9_5b_r_staff_locale_preference

Revision ID: 338d06dece44
Revises: af7831a6dc4d
Create Date: 2026-08-01 11:03:31.920915

"""
from alembic import op
import sqlalchemy as sa


revision = '338d06dece44'
down_revision = 'af7831a6dc4d'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('owner_staff_users', sa.Column('locale', sa.String(length=8), nullable=True))
    # Alembic's default autogenerate does not diff CHECK constraints -- added
    # by hand, matching the real model definition (app/models/staff.py).
    op.create_check_constraint(
        'ck_staff_users_locale_supported', 'owner_staff_users', "locale IS NULL OR locale IN ('en', 'ar')"
    )


def downgrade() -> None:
    op.drop_constraint('ck_staff_users_locale_supported', 'owner_staff_users', type_='check')
    op.drop_column('owner_staff_users', 'locale')
