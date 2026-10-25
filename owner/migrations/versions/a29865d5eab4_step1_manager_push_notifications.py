"""step1_manager_push_notifications (Step 1, 2026-10-25)

Revision ID: a29865d5eab4
Revises: f28dfb3efc40
Create Date: 2026-10-25 18:00:00.000000

When a seller records a sale, every manager who may confirm it gets a push
notification on their phone (app/push/, read app/models/push.py for the
state machines). Three NEW tables; no existing table is altered:

  owner_push_subscriptions  one row per staff device that turned
                            notifications on (endpoint unique)
  owner_push_outbox         one row per sale waiting for a manager, written in
                            the same transaction as the sale's PENDING payment
                            (unique per event type + sale)
  owner_push_deliveries     one row per (outbox row, device) (unique), its
                            device FK SET NULL so a removed device keeps the
                            record of what was sent; a QUEUED row must have a
                            device (CHECK)

Safe with the old code running: it never reads or writes these tables.
The downgrade drops them. What is lost is notification state only -- which
phones opted in (they turn it on again) and which sales were already pushed --
never money, sales or licence data.

a29865d5eab4 was claimed in ROADMAP.md before this migration was written
(single-writer resource), commit 5a743d75.
"""
from alembic import op
import sqlalchemy as sa


revision = 'a29865d5eab4'
down_revision = 'f28dfb3efc40'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'owner_push_subscriptions',
        sa.Column('staff_user_id', sa.UUID(), nullable=False),
        sa.Column('endpoint', sa.Text(), nullable=False),
        sa.Column('p256dh', sa.String(length=128), nullable=False),
        sa.Column('auth', sa.String(length=64), nullable=False),
        sa.Column('locale', sa.String(length=8), nullable=True),
        sa.Column('user_agent', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('last_sent_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('lease_expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(['staff_user_id'], ['owner_staff_users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('endpoint', name='uq_owner_push_subscriptions_endpoint'),
    )
    op.create_index('ix_owner_push_subscriptions_staff_user_id', 'owner_push_subscriptions', ['staff_user_id'], unique=False)

    op.create_table(
        'owner_push_outbox',
        sa.Column('event_type', sa.String(length=48), nullable=False),
        sa.Column('sales_order_id', sa.UUID(), nullable=False),
        sa.Column('payment_record_id', sa.UUID(), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('skip_reason', sa.String(length=48), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('processed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.CheckConstraint("status IN ('PENDING', 'EXPANDED', 'SKIPPED')", name='ck_owner_push_outbox_status'),
        sa.ForeignKeyConstraint(['sales_order_id'], ['owner_sales_orders.id']),
        sa.ForeignKeyConstraint(['payment_record_id'], ['owner_payment_records.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('event_type', 'sales_order_id', name='uq_owner_push_outbox_event_sale'),
    )
    op.create_index('ix_owner_push_outbox_status_created', 'owner_push_outbox', ['status', 'created_at'], unique=False)

    op.create_table(
        'owner_push_deliveries',
        sa.Column('outbox_id', sa.UUID(), nullable=False),
        sa.Column('subscription_id', sa.UUID(), nullable=True),
        sa.Column('staff_user_id', sa.UUID(), nullable=False),
        sa.Column('status', sa.String(length=24), nullable=False),
        sa.Column('attempt_count', sa.Integer(), nullable=False),
        sa.Column('next_attempt_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_error', sa.String(length=48), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.CheckConstraint(
            "status IN ('QUEUED', 'SENT', 'FAILED_PERMANENT', 'CANCELLED')", name='ck_owner_push_deliveries_status'
        ),
        sa.CheckConstraint(
            "status <> 'QUEUED' OR subscription_id IS NOT NULL", name='ck_owner_push_deliveries_queued_has_device'
        ),
        sa.ForeignKeyConstraint(['outbox_id'], ['owner_push_outbox.id']),
        sa.ForeignKeyConstraint(['subscription_id'], ['owner_push_subscriptions.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['staff_user_id'], ['owner_staff_users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('outbox_id', 'subscription_id', name='uq_owner_push_deliveries_outbox_subscription'),
    )
    op.create_index('ix_owner_push_deliveries_outbox_id', 'owner_push_deliveries', ['outbox_id'], unique=False)
    op.create_index('ix_owner_push_deliveries_subscription_id', 'owner_push_deliveries', ['subscription_id'], unique=False)
    op.create_index(
        'ix_owner_push_deliveries_status_next_attempt', 'owner_push_deliveries', ['status', 'next_attempt_at'], unique=False
    )


def downgrade() -> None:
    op.drop_index('ix_owner_push_deliveries_status_next_attempt', table_name='owner_push_deliveries')
    op.drop_index('ix_owner_push_deliveries_subscription_id', table_name='owner_push_deliveries')
    op.drop_index('ix_owner_push_deliveries_outbox_id', table_name='owner_push_deliveries')
    op.drop_table('owner_push_deliveries')
    op.drop_index('ix_owner_push_outbox_status_created', table_name='owner_push_outbox')
    op.drop_table('owner_push_outbox')
    op.drop_index('ix_owner_push_subscriptions_staff_user_id', table_name='owner_push_subscriptions')
    op.drop_table('owner_push_subscriptions')
