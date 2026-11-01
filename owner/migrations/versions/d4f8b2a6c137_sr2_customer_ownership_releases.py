"""sr2_customer_ownership_releases (seller agreement clause 8: a customer's 15-day lapse, 2026-11-01)

Revision ID: d4f8b2a6c137
Revises: c3e7a1f4b926
Create Date: 2026-11-01 15:00:00.000000

One table, owner_customer_ownership_releases: the record that a customer stopped belonging to a seller
(the 15 days ran out, or the seller's agreement ended) and what became of it (claimed by another seller,
or handed over by a manager). A customer is OPEN while its newest record is unclosed and nobody owns it.
A PARTIAL UNIQUE INDEX allows only one unclosed record per customer, so "open" can never be ambiguous.

One data row: owner_system_settings key `seller_customer_lapse_started_at` = the moment this migration ran.
The 15-day clock never starts earlier than that, so the customers registered before this rule existed do
not all lapse on the first night it runs. (Insert is ON CONFLICT DO NOTHING: re-running changes nothing.)

Otherwise additive: a new table nothing else reads. DOWNGRADE refuses while any release record exists
(it is the history of who lost which customer) and removes the settings row it added.

Second of the seller-rules revisions chained on branch feat/seller-rules.
"""
import json
import uuid
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa


revision = 'd4f8b2a6c137'  # pragma: allowlist secret -- an Alembic revision id, not a credential
down_revision = 'c3e7a1f4b926'  # pragma: allowlist secret -- an Alembic revision id, not a credential
branch_labels = None
depends_on = None

TABLE = 'owner_customer_ownership_releases'
SETTING_KEY = 'seller_customer_lapse_started_at'


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column('customer_id', sa.UUID(), nullable=False),
        sa.Column('previous_owner_staff_user_id', sa.UUID(), nullable=True),
        sa.Column('reason', sa.String(length=32), nullable=False),
        sa.Column('released_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('closed_how', sa.String(length=16), nullable=True),
        sa.Column('claimed_by_staff_user_id', sa.UUID(), nullable=True),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("reason IN ('LAPSED_15_DAYS', 'AGREEMENT_ENDED')", name='ck_owner_customer_ownership_releases_reason'),
        sa.CheckConstraint(
            "closed_how IS NULL OR closed_how IN ('CLAIMED', 'REASSIGNED', 'SUPERSEDED')",
            name='ck_owner_customer_ownership_releases_closed_how'),
        sa.CheckConstraint(
            '(closed_at IS NULL) = (closed_how IS NULL)', name='ck_owner_customer_ownership_releases_closed_pair'),
        sa.ForeignKeyConstraint(['customer_id'], ['owner_customers.id']),
        sa.ForeignKeyConstraint(['previous_owner_staff_user_id'], ['owner_staff_users.id']),
        sa.ForeignKeyConstraint(['claimed_by_staff_user_id'], ['owner_staff_users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_owner_customer_ownership_releases_customer_id'), TABLE, ['customer_id'], unique=False)
    op.create_index(
        'uq_owner_customer_ownership_releases_one_open', TABLE, ['customer_id'], unique=True,
        postgresql_where=sa.text('closed_at IS NULL'))
    bind = op.get_bind()
    bind.execute(
        sa.text(
            'INSERT INTO owner_system_settings (id, key, value, created_at, updated_at) '
            'VALUES (:id, :key, CAST(:value AS JSONB), now(), now()) ON CONFLICT (key) DO NOTHING'),
        {'id': str(uuid.uuid4()), 'key': SETTING_KEY,
         'value': json.dumps({'started_at': datetime.now(timezone.utc).isoformat()})},
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.execute(sa.text(f'SELECT 1 FROM {TABLE} LIMIT 1')).first() is not None:
        raise RuntimeError(
            'Refusing to downgrade d4f8b2a6c137: owner_customer_ownership_releases holds the history of customers '
            'sellers lost (and who took them). Export and delete the rows first.')
    bind.execute(sa.text('DELETE FROM owner_system_settings WHERE key = :key'), {'key': SETTING_KEY})
    op.drop_index('uq_owner_customer_ownership_releases_one_open', table_name=TABLE)
    op.drop_index(op.f('ix_owner_customer_ownership_releases_customer_id'), table_name=TABLE)
    op.drop_table(TABLE)
