"""step1_commission_clawback_deductions (Step 1, 2026-10-25)

Revision ID: c7d41e90b2a5
Revises: a6e81bf3d32c
Create Date: 2026-10-25 20:00:00.000000

Owner decision 2026-10-25: a commission taken back after the seller was
already paid is deducted automatically from the seller's next pay-out. One NEW
table, nothing existing is altered:

  owner_commission_clawback_deductions  one row per amount taken off a debt
      (a REVERSED ledger row whose parent entry was PAID) -- from a pay-out
      line (kind PAYOUT) or settled by hand by a Super Admin (kind HAND, a
      written reason mandatory). amount > 0; the kind/shape CHECK is spelled
      with IS [NOT] NULL so the database, not only the code, refuses a HAND
      row without a reason or a PAYOUT row without its line and batch.

Safe with the old code running: it never reads or writes the table (it would
simply keep paying the full amount, as before).

DOWNGRADE REFUSES while any row exists: the rows are the record of money
already withheld from sellers; dropping them would make those debts look
unpaid again and the next pay-out would take the money a second time.

c7d41e90b2a5 was claimed in ROADMAP.md before this migration was written
(single-writer resource), commit ab60f513.
"""
from alembic import op
import sqlalchemy as sa


revision = 'c7d41e90b2a5'
down_revision = 'a6e81bf3d32c'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'owner_commission_clawback_deductions',
        sa.Column('debt_ledger_entry_id', sa.UUID(), nullable=False),
        sa.Column('payout_line_id', sa.UUID(), nullable=True),
        sa.Column('commission_payout_batch_id', sa.UUID(), nullable=True),
        sa.Column('employee_profile_id', sa.UUID(), nullable=False),
        sa.Column('amount', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('currency', sa.String(length=3), nullable=False),
        sa.Column('kind', sa.String(length=8), nullable=False),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('created_by_staff_user_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.CheckConstraint('amount > 0', name='ck_owner_commission_clawback_deductions_amount_positive'),
        sa.CheckConstraint("kind IN ('PAYOUT', 'HAND')", name='ck_owner_commission_clawback_deductions_kind'),
        sa.CheckConstraint(
            "(kind = 'PAYOUT' AND payout_line_id IS NOT NULL AND commission_payout_batch_id IS NOT NULL) "
            "OR (kind = 'HAND' AND payout_line_id IS NULL AND commission_payout_batch_id IS NULL "
            "AND reason IS NOT NULL AND length(btrim(reason)) > 0)",
            name='ck_owner_commission_clawback_deductions_kind_shape',
        ),
        sa.ForeignKeyConstraint(['debt_ledger_entry_id'], ['owner_commission_ledger_entries.id']),
        sa.ForeignKeyConstraint(['payout_line_id'], ['owner_commission_payout_lines.id']),
        sa.ForeignKeyConstraint(['commission_payout_batch_id'], ['owner_commission_payout_batches.id']),
        sa.ForeignKeyConstraint(['employee_profile_id'], ['owner_employee_profiles.id']),
        sa.ForeignKeyConstraint(['created_by_staff_user_id'], ['owner_staff_users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_owner_commission_clawback_deductions_debt', 'owner_commission_clawback_deductions', ['debt_ledger_entry_id'], unique=False)
    op.create_index('ix_owner_commission_clawback_deductions_line', 'owner_commission_clawback_deductions', ['payout_line_id'], unique=False)
    op.create_index('ix_owner_commission_clawback_deductions_employee', 'owner_commission_clawback_deductions', ['employee_profile_id'], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.execute(sa.text('SELECT 1 FROM owner_commission_clawback_deductions LIMIT 1')).first() is not None:
        raise RuntimeError(
            'Refusing to downgrade c7d41e90b2a5: owner_commission_clawback_deductions still holds the record of '
            'money already withheld from sellers; dropping it would make those debts look unpaid and the next '
            'pay-out would take them a second time. Empty it deliberately first.'
        )
    op.drop_index('ix_owner_commission_clawback_deductions_employee', table_name='owner_commission_clawback_deductions')
    op.drop_index('ix_owner_commission_clawback_deductions_line', table_name='owner_commission_clawback_deductions')
    op.drop_index('ix_owner_commission_clawback_deductions_debt', table_name='owner_commission_clawback_deductions')
    op.drop_table('owner_commission_clawback_deductions')
