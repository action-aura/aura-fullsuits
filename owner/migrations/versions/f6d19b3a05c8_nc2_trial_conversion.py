"""nc2_trial_conversion (new customer flow, slice S3, 2026-10-31)

Revision ID: f6d19b3a05c8
Revises: e2c4a8b1f730
Create Date: 2026-10-31 15:00:00.000000

A customer pays for the trial they already have. The sale (a SalesOrder made by
the one-screen sale form) names the trial's subscription; when a manager
confirms the payment, fulfillment converts THAT subscription in place (PILOT ->
ACTIVE, new term, new package) and keeps the SAME licence and key instead of
making new ones.

  owner_sales_orders.converts_subscription_id uuid NULL FK owner_subscriptions
  uq_owner_sales_orders_one_live_conversion_per_trial
      UNIQUE (converts_subscription_id) WHERE converts_subscription_id IS NOT NULL
      AND status <> 'CANCELLED'
      -- one live order per trial; a cancelled one (payment rejected) frees it.

Safe with the old code running: a nullable column nothing reads; every existing
order has NULL there, so the partial index has no rows to conflict.

DOWNGRADE REFUSES while any order names a trial: dropping the column would turn a
paid conversion back into an ordinary new-subscription sale in the history.

f6d19b3a05c8 was claimed in ROADMAP.md before this file was written
(single-writer resource), together with e2c4a8b1f730.
"""
from alembic import op
import sqlalchemy as sa


revision = 'f6d19b3a05c8'
down_revision = 'e2c4a8b1f730'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('owner_sales_orders', sa.Column('converts_subscription_id', sa.UUID(), nullable=True))
    op.create_foreign_key(
        'fk_owner_sales_orders_converts_subscription', 'owner_sales_orders', 'owner_subscriptions',
        ['converts_subscription_id'], ['id'])
    op.create_index('ix_owner_sales_orders_converts_subscription_id', 'owner_sales_orders', ['converts_subscription_id'], unique=False)
    op.create_index(
        'uq_owner_sales_orders_one_live_conversion_per_trial', 'owner_sales_orders', ['converts_subscription_id'], unique=True,
        postgresql_where=sa.text("converts_subscription_id IS NOT NULL AND status <> 'CANCELLED'"))


def downgrade() -> None:
    bind = op.get_bind()
    if bind.execute(sa.text('SELECT 1 FROM owner_sales_orders WHERE converts_subscription_id IS NOT NULL LIMIT 1')).first() is not None:
        raise RuntimeError(
            'Refusing to downgrade f6d19b3a05c8: sales orders still name the trial they convert; a rollback would '
            'turn those paid conversions into ordinary sales. Remove them deliberately first.'
        )
    op.drop_index('uq_owner_sales_orders_one_live_conversion_per_trial', table_name='owner_sales_orders')
    op.drop_index('ix_owner_sales_orders_converts_subscription_id', table_name='owner_sales_orders')
    op.drop_constraint('fk_owner_sales_orders_converts_subscription', 'owner_sales_orders', type_='foreignkey')
    op.drop_column('owner_sales_orders', 'converts_subscription_id')
