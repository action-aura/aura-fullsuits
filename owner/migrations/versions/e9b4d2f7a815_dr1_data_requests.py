"""dr1_data_requests (personal-data requests: copy / erasure, 2026-11-01)

Revision ID: e9b4d2f7a815
Revises: c7d3e9a1b624
Create Date: 2026-11-01 21:00:00.000000

One new table, owner_data_requests: a customer's request for a copy of what we hold about them
(EXPORT) or for it to be deleted (ERASE), logged the day it arrives with its 30-day due date and
closed with a written answer. The row holds no personal data of the customer (it points at the
customer by id); it must outlive the erasure it records, because the proof that we answered, and
when, is what survives an erasure.

Purely additive: nothing is altered, old code running against the new schema is unaffected.
DOWNGRADE refuses while any row exists (the records are the company's proof of answering).
"""
from alembic import op
import sqlalchemy as sa


revision = 'e9b4d2f7a815'  # pragma: allowlist secret -- an Alembic revision id, not a credential
down_revision = 'c7d3e9a1b624'  # pragma: allowlist secret -- an Alembic revision id, not a credential
branch_labels = None
depends_on = None

TABLE = 'owner_data_requests'


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('request_number', sa.Integer(), sa.Identity(always=False), nullable=False),
        sa.Column('customer_id', sa.UUID(), nullable=False),
        sa.Column('kind', sa.String(length=8), nullable=False),
        sa.Column('channel', sa.String(length=12), nullable=False),
        sa.Column('received_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('due_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('status', sa.String(length=8), server_default='OPEN', nullable=False),
        sa.Column('answer_note', sa.Text(), nullable=True),
        sa.Column('logged_by_staff_user_id', sa.UUID(), nullable=False),
        sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('closed_by_staff_user_id', sa.UUID(), nullable=True),
        sa.Column('fulfilled_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("kind IN ('EXPORT', 'ERASE')", name='ck_owner_data_requests_kind'),
        sa.CheckConstraint("channel IN ('EMAIL', 'PHONE', 'IN_PERSON')", name='ck_owner_data_requests_channel'),
        sa.CheckConstraint("status IN ('OPEN', 'DONE', 'REFUSED')", name='ck_owner_data_requests_status'),
        sa.CheckConstraint('due_at >= received_at', name='ck_owner_data_requests_due_after_received'),
        sa.CheckConstraint("(status = 'OPEN') = (closed_at IS NULL)", name='ck_owner_data_requests_closed_time'),
        sa.CheckConstraint(
            "status = 'OPEN' OR length(btrim(coalesce(answer_note, ''))) > 0", name='ck_owner_data_requests_answer_note'),
        sa.CheckConstraint(
            "NOT (kind = 'ERASE' AND status = 'DONE') OR fulfilled_at IS NOT NULL", name='ck_owner_data_requests_erase_done'),
        sa.ForeignKeyConstraint(['customer_id'], ['owner_customers.id']),
        sa.ForeignKeyConstraint(['logged_by_staff_user_id'], ['owner_staff_users.id']),
        sa.ForeignKeyConstraint(['closed_by_staff_user_id'], ['owner_staff_users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('request_number'),
    )
    op.create_index(op.f('ix_owner_data_requests_customer_id'), TABLE, ['customer_id'], unique=False)
    op.create_index('ix_owner_data_requests_status_due', TABLE, ['status', 'due_at'], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.execute(sa.text(f'SELECT 1 FROM {TABLE} LIMIT 1')).first() is not None:
        raise RuntimeError(
            f'Refusing to downgrade e9b4d2f7a815: {TABLE} holds the company\'s record of answering '
            'personal-data requests. Export and delete them first.')
    op.drop_index('ix_owner_data_requests_status_due', table_name=TABLE)
    op.drop_index(op.f('ix_owner_data_requests_customer_id'), table_name=TABLE)
    op.drop_table(TABLE)
