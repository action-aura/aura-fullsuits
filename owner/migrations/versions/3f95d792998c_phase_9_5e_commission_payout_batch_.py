"""phase_9_5e_commission_payout_batch_payment_method

Revision ID: 3f95d792998c
Revises: e8fb57c82a58
Create Date: 2026-08-02 21:05:28.372192

"""
from alembic import op
import sqlalchemy as sa


revision = '3f95d792998c'
down_revision = 'e8fb57c82a58'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Additive only -- Cash Closing needs to know which commission payout
    # batches were paid in cash (docs/owner/phase9_5e/cash-closing-contract.md).
    # No prior 9.5D behavior depended on this column's absence; existing rows
    # get NULL, treated as "not cash" (conservative -- never overstates a
    # cash outflow).
    op.add_column("owner_commission_payout_batches", sa.Column("payment_method", sa.String(32)))


def downgrade() -> None:
    op.drop_column("owner_commission_payout_batches", "payment_method")
