"""so1_commission_statement_objections (seller agreement clause 5, builder A3, 2026-10-31)

Revision ID: d7e3a1b94c52
Revises: a3c7e9f15b28
Create Date: 2026-10-31 20:00:00.000000

Clause 5 of the seller agreement: the seller gets a monthly commission statement in
Aura Owner and may OBJECT within 7 days of it being issued; the company answers within
2 working days. The statement itself is derived from the commission ledger (nothing
stored); this table holds the objection, the statement as the seller saw it, and the
company's written answer.

The deadlines are guarded by the database, not only by code:
  objected_at < window_closes_at   the 7-day window
  due_at > objected_at             the reply deadline is in the future of the objection
  answer shape                     open <=> no answer; answered <=> reply + answerer + time
  answered_by <> staff_user        nobody answers their own objection
  UNIQUE (staff_user_id, statement_month)   one objection per seller per statement

Purely additive: a new table. Old code running against the new schema is unaffected.
DOWNGRADE drops the table (and so deletes every objection and reply): refuses while any
row exists, because that is the seller's evidence.

d7e3a1b94c52 was claimed in ROADMAP.md before this file was written (single-writer resource).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = 'd7e3a1b94c52'  # pragma: allowlist secret -- an Alembic revision id, not a credential
down_revision = 'a3c7e9f15b28'  # pragma: allowlist secret -- an Alembic revision id, not a credential
branch_labels = None
depends_on = None

TABLE = 'owner_commission_statement_objections'


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column('staff_user_id', sa.UUID(), nullable=False),
        sa.Column('employee_profile_id', sa.UUID(), nullable=False),
        sa.Column('statement_month', sa.Date(), nullable=False),
        sa.Column('reason', sa.Text(), nullable=False),
        sa.Column('statement_snapshot', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('statement_sha256', sa.String(length=64), nullable=False),
        sa.Column('objected_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('window_closes_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('due_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('status', sa.String(length=12), server_default='OPEN', nullable=False),
        sa.Column('reply_text', sa.Text(), nullable=True),
        sa.Column('answered_by_staff_user_id', sa.UUID(), nullable=True),
        sa.Column('answered_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("status IN ('OPEN', 'ACCEPTED', 'REJECTED')", name='ck_owner_commission_statement_objections_status'),
        sa.CheckConstraint('length(btrim(reason)) > 0', name='ck_owner_commission_statement_objections_reason'),
        sa.CheckConstraint('extract(day FROM statement_month) = 1', name='ck_owner_commission_statement_objections_month_first'),
        sa.CheckConstraint('objected_at < window_closes_at', name='ck_owner_commission_statement_objections_in_window'),
        sa.CheckConstraint('due_at > objected_at', name='ck_owner_commission_statement_objections_due_after'),
        sa.CheckConstraint(
            "(status = 'OPEN' AND answered_at IS NULL AND answered_by_staff_user_id IS NULL AND reply_text IS NULL) OR "
            "(status IN ('ACCEPTED', 'REJECTED') AND answered_at IS NOT NULL AND answered_by_staff_user_id IS NOT NULL "
            "AND reply_text IS NOT NULL AND length(btrim(reply_text)) > 0)",
            name='ck_owner_commission_statement_objections_answer_shape'),
        sa.CheckConstraint(
            'answered_by_staff_user_id IS NULL OR answered_by_staff_user_id <> staff_user_id',
            name='ck_owner_commission_statement_objections_not_self_answered'),
        sa.ForeignKeyConstraint(['staff_user_id'], ['owner_staff_users.id']),
        sa.ForeignKeyConstraint(['employee_profile_id'], ['owner_employee_profiles.id']),
        sa.ForeignKeyConstraint(['answered_by_staff_user_id'], ['owner_staff_users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('staff_user_id', 'statement_month', name='uq_owner_commission_statement_objections_one_per_month'),
    )
    op.create_index(op.f('ix_owner_commission_statement_objections_staff_user_id'), TABLE, ['staff_user_id'], unique=False)
    op.create_index('ix_owner_commission_statement_objections_status_due', TABLE, ['status', 'due_at'], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.execute(sa.text(f'SELECT 1 FROM {TABLE} LIMIT 1')).first() is not None:
        raise RuntimeError(
            'Refusing to drop owner_commission_statement_objections: it holds sellers\' objections and the '
            'company\'s replies (evidence). Export them first.')
    op.drop_index('ix_owner_commission_statement_objections_status_due', table_name=TABLE)
    op.drop_index(op.f('ix_owner_commission_statement_objections_staff_user_id'), table_name=TABLE)
    op.drop_table(TABLE)
