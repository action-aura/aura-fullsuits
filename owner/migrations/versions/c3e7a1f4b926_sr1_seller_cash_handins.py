"""sr1_seller_cash_handins (seller agreement clause 7: cash back to the company in 24 hours, 2026-11-01)

Revision ID: c3e7a1f4b926
Revises: b5a9c2d7e418
Create Date: 2026-11-01 14:00:00.000000

One table, owner_seller_cash_handins: one row per CASH payment a seller recorded. The row is the
obligation (due 24 hours after collection) and its closing (bank-transfer slip checked by a manager,
or cash received at the office). UNIQUE on payment_record_id makes "exactly one obligation per
payment" a database fact; CHECKs pin the status vocabulary and the slip/received invariants.

Purely additive: a new table nothing else reads. Old code running against the new schema is unaffected.
Existing cash payments get NO row (the rule starts when this ships; nothing is back-filled).
DOWNGRADE refuses while any row exists: it is the record of money a seller owed the company.

This is the first of the seller-rules revisions chained on branch feat/seller-rules; the next one
re-points its down_revision at this id.
"""
from alembic import op
import sqlalchemy as sa


revision = 'c3e7a1f4b926'  # pragma: allowlist secret -- an Alembic revision id, not a credential
down_revision = 'b5a9c2d7e418'  # pragma: allowlist secret -- an Alembic revision id, not a credential
branch_labels = None
depends_on = None

TABLE = 'owner_seller_cash_handins'


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column('seller_staff_user_id', sa.UUID(), nullable=False),
        sa.Column('payment_record_id', sa.UUID(), nullable=False),
        sa.Column('amount', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('currency', sa.String(length=8), nullable=False),
        sa.Column('collected_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('due_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('status', sa.String(length=24), nullable=False),
        sa.Column('slip_document_id', sa.UUID(), nullable=True),
        sa.Column('submitted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('received_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('received_by_staff_user_id', sa.UUID(), nullable=True),
        sa.Column('rejected_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('last_rejection_reason', sa.Text(), nullable=True),
        sa.Column('overdue_notified_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("status IN ('OPEN', 'SUBMITTED', 'RECEIVED', 'RECEIVED_AT_OFFICE')", name='ck_owner_seller_cash_handins_status'),
        sa.CheckConstraint('amount > 0', name='ck_owner_seller_cash_handins_amount_positive'),
        sa.CheckConstraint('due_at > collected_at', name='ck_owner_seller_cash_handins_due_after_collected'),
        sa.CheckConstraint("status <> 'SUBMITTED' OR slip_document_id IS NOT NULL", name='ck_owner_seller_cash_handins_submitted_has_slip'),
        sa.CheckConstraint("status <> 'OPEN' OR slip_document_id IS NULL", name='ck_owner_seller_cash_handins_open_has_no_slip'),
        sa.CheckConstraint(
            "(status IN ('RECEIVED', 'RECEIVED_AT_OFFICE')) = (received_at IS NOT NULL AND received_by_staff_user_id IS NOT NULL)",
            name='ck_owner_seller_cash_handins_received_stamped'),
        sa.ForeignKeyConstraint(['seller_staff_user_id'], ['owner_staff_users.id']),
        sa.ForeignKeyConstraint(['payment_record_id'], ['owner_payment_records.id']),
        sa.ForeignKeyConstraint(['slip_document_id'], ['owner_customer_documents.id']),
        sa.ForeignKeyConstraint(['received_by_staff_user_id'], ['owner_staff_users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('payment_record_id'),
    )
    op.create_index(op.f('ix_owner_seller_cash_handins_seller_staff_user_id'), TABLE, ['seller_staff_user_id'], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.execute(sa.text(f'SELECT 1 FROM {TABLE} LIMIT 1')).first() is not None:
        raise RuntimeError(
            'Refusing to downgrade c3e7a1f4b926: owner_seller_cash_handins holds the record of cash sellers owed '
            'the company. Export and delete the rows first.')
    op.drop_index(op.f('ix_owner_seller_cash_handins_seller_staff_user_id'), table_name=TABLE)
    op.drop_table(TABLE)
