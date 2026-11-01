"""disc1_seller_discipline (sanctions and the end of a seller's agreement, 2026-11-01)

Revision ID: c7d3e9a1b624
Revises: b5a9c2d7e418
Create Date: 2026-11-01 12:00:00.000000

Seller agreement clauses 16, 17 and 18:

  owner_sanction_cases        a sanction case (warning / suspension / commission withhold /
                              termination) with the seller's 2-working-day reply. The row IS the
                              "commission withheld" flag for its deal -- no second table.
  owner_seller_terminations   the ending of a seller's agreement: 7 days' notice from either side,
                              or immediate from a sanction. At most ONE running notice per seller
                              (partial unique index).
  owner_seller_trial_credits  clause 18: a trial that STARTED before the termination keeps crediting
                              the seller for its first payment. One row per trial subscription.

Purely additive: three new tables, nothing is altered. Old code running against the new schema is
unaffected. DOWNGRADE refuses while any row exists: these rows are the company's disciplinary and
termination record (and owner_seller_trial_credits is what keeps a former seller's commission due).

NOTE FOR WHOEVER CHAINS THIS ON: `down_revision` below is the only line that decides where this
migration sits. Two other branches also add one revision on top of b5a9c2d7e418; re-chain at merge.
"""
from alembic import op
import sqlalchemy as sa


# --- the two lines to re-chain -------------------------------------------------
revision = 'c7d3e9a1b624'  # pragma: allowlist secret -- an Alembic revision id, not a credential
# ---
down_revision = 'd7c3f1a9e254'  # chained at merge (was b5a9c2d7e418)  # pragma: allowlist secret -- an Alembic revision id, not a credential
# -------------------------------------------------------------------------------
branch_labels = None
depends_on = None

CASES = 'owner_sanction_cases'
TERMINATIONS = 'owner_seller_terminations'
CREDITS = 'owner_seller_trial_credits'


def _stamps():
    return [
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        CASES,
        sa.Column('case_number', sa.Integer(), sa.Identity(always=False), nullable=False),
        sa.Column('seller_staff_user_id', sa.UUID(), nullable=False),
        sa.Column('opened_by_staff_user_id', sa.UUID(), nullable=False),
        sa.Column('kind', sa.String(length=24), nullable=False),
        sa.Column('reason_category', sa.String(length=32), server_default='OTHER', nullable=False),
        sa.Column('reason_text', sa.Text(), nullable=False),
        sa.Column('link_kind', sa.String(length=16), nullable=True),
        sa.Column('link_id', sa.UUID(), nullable=True),
        sa.Column('suspension_until', sa.DateTime(timezone=True), nullable=True),
        sa.Column('withheld_sales_order_id', sa.UUID(), nullable=True),
        sa.Column('status', sa.String(length=16), server_default='NOTICE_SENT', nullable=False),
        sa.Column('immediate', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('notice_sent_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('response_due_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('response_text', sa.Text(), nullable=True),
        sa.Column('responded_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('decided_by_staff_user_id', sa.UUID(), nullable=True),
        sa.Column('decision_note', sa.Text(), nullable=True),
        sa.Column('suspension_ended_at', sa.DateTime(timezone=True), nullable=True),
        *_stamps(),
        sa.CheckConstraint("kind IN ('WARNING', 'SUSPENSION', 'COMMISSION_WITHHOLD', 'TERMINATION')", name='ck_owner_sanction_cases_kind'),
        sa.CheckConstraint("status IN ('NOTICE_SENT', 'RESPONDED', 'DECIDED_APPLIED', 'DECIDED_DROPPED')", name='ck_owner_sanction_cases_status'),
        sa.CheckConstraint(
            "reason_category IN ('MONEY_MANIPULATION', 'CONFIDENTIAL_DISCLOSURE', 'REVERSE_ENGINEERING', 'OTHER')",
            name='ck_owner_sanction_cases_category'),
        sa.CheckConstraint('length(btrim(reason_text)) > 0', name='ck_owner_sanction_cases_reason_text'),
        sa.CheckConstraint("kind <> 'SUSPENSION' OR suspension_until IS NOT NULL", name='ck_owner_sanction_cases_suspension_date'),
        sa.CheckConstraint(
            "kind <> 'COMMISSION_WITHHOLD' OR withheld_sales_order_id IS NOT NULL", name='ck_owner_sanction_cases_withhold_order'),
        sa.CheckConstraint(
            "(status IN ('DECIDED_APPLIED', 'DECIDED_DROPPED')) = (decided_at IS NOT NULL)", name='ck_owner_sanction_cases_decided_time'),
        sa.CheckConstraint('immediate OR response_due_at IS NOT NULL', name='ck_owner_sanction_cases_deadline'),
        sa.CheckConstraint(
            "NOT immediate OR (kind = 'TERMINATION' AND status = 'DECIDED_APPLIED')", name='ck_owner_sanction_cases_immediate'),
        sa.ForeignKeyConstraint(['seller_staff_user_id'], ['owner_staff_users.id']),
        sa.ForeignKeyConstraint(['opened_by_staff_user_id'], ['owner_staff_users.id']),
        sa.ForeignKeyConstraint(['decided_by_staff_user_id'], ['owner_staff_users.id']),
        sa.ForeignKeyConstraint(['withheld_sales_order_id'], ['owner_sales_orders.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('case_number'),
    )
    op.create_index(op.f('ix_owner_sanction_cases_seller_staff_user_id'), CASES, ['seller_staff_user_id'], unique=False)
    op.create_index('ix_owner_sanction_cases_seller_status', CASES, ['seller_staff_user_id', 'status'], unique=False)
    op.create_index('ix_owner_sanction_cases_withheld_order', CASES, ['withheld_sales_order_id'], unique=False)

    op.create_table(
        TERMINATIONS,
        sa.Column('seller_staff_user_id', sa.UUID(), nullable=False),
        sa.Column('initiated_by_staff_user_id', sa.UUID(), nullable=False),
        sa.Column('initiated_by_seller', sa.Boolean(), nullable=False),
        sa.Column('sanction_case_id', sa.UUID(), nullable=True),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('notice_given_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('effective_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('reassign_to_staff_user_id', sa.UUID(), nullable=True),
        sa.Column('status', sa.String(length=8), server_default='NOTICE', nullable=False),
        sa.Column('applied_at', sa.DateTime(timezone=True), nullable=True),
        *_stamps(),
        sa.CheckConstraint("status IN ('NOTICE', 'APPLIED')", name='ck_owner_seller_terminations_status'),
        sa.CheckConstraint('effective_at >= notice_given_at', name='ck_owner_seller_terminations_dates'),
        sa.CheckConstraint("(status = 'APPLIED') = (applied_at IS NOT NULL)", name='ck_owner_seller_terminations_applied_time'),
        sa.ForeignKeyConstraint(['seller_staff_user_id'], ['owner_staff_users.id']),
        sa.ForeignKeyConstraint(['initiated_by_staff_user_id'], ['owner_staff_users.id']),
        sa.ForeignKeyConstraint(['sanction_case_id'], ['owner_sanction_cases.id']),
        sa.ForeignKeyConstraint(['reassign_to_staff_user_id'], ['owner_staff_users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_owner_seller_terminations_seller_staff_user_id'), TERMINATIONS, ['seller_staff_user_id'], unique=False)
    op.create_index(
        'uq_owner_seller_terminations_one_notice', TERMINATIONS, ['seller_staff_user_id'], unique=True,
        postgresql_where=sa.text("status = 'NOTICE'"))
    op.create_index('ix_owner_seller_terminations_status_effective', TERMINATIONS, ['status', 'effective_at'], unique=False)

    op.create_table(
        CREDITS,
        sa.Column('termination_id', sa.UUID(), nullable=False),
        sa.Column('seller_staff_user_id', sa.UUID(), nullable=False),
        sa.Column('customer_id', sa.UUID(), nullable=False),
        sa.Column('subscription_id', sa.UUID(), nullable=False),
        sa.Column('released_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('release_reason', sa.String(length=16), nullable=True),
        *_stamps(),
        sa.CheckConstraint("release_reason IS NULL OR release_reason IN ('CONVERTED', 'TRIAL_ENDED')", name='ck_owner_seller_trial_credits_reason'),
        sa.CheckConstraint('(released_at IS NULL) = (release_reason IS NULL)', name='ck_owner_seller_trial_credits_released'),
        sa.ForeignKeyConstraint(['termination_id'], [f'{TERMINATIONS}.id']),
        sa.ForeignKeyConstraint(['seller_staff_user_id'], ['owner_staff_users.id']),
        sa.ForeignKeyConstraint(['customer_id'], ['owner_customers.id']),
        sa.ForeignKeyConstraint(['subscription_id'], ['owner_subscriptions.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('subscription_id'),
    )
    op.create_index(op.f('ix_owner_seller_trial_credits_seller_staff_user_id'), CREDITS, ['seller_staff_user_id'], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    for table in (CREDITS, TERMINATIONS, CASES):
        if bind.execute(sa.text(f'SELECT 1 FROM {table} LIMIT 1')).first() is not None:
            raise RuntimeError(
                f'Refusing to downgrade c7d3e9a1b624: {table} holds disciplinary / termination records (and the '
                'trial credits that keep a former seller\'s first-payment commission due). Export and delete them first.')
    op.drop_index(op.f('ix_owner_seller_trial_credits_seller_staff_user_id'), table_name=CREDITS)
    op.drop_table(CREDITS)
    op.drop_index('ix_owner_seller_terminations_status_effective', table_name=TERMINATIONS)
    op.drop_index('uq_owner_seller_terminations_one_notice', table_name=TERMINATIONS)
    op.drop_index(op.f('ix_owner_seller_terminations_seller_staff_user_id'), table_name=TERMINATIONS)
    op.drop_table(TERMINATIONS)
    op.drop_index('ix_owner_sanction_cases_withheld_order', table_name=CASES)
    op.drop_index('ix_owner_sanction_cases_seller_status', table_name=CASES)
    op.drop_index(op.f('ix_owner_sanction_cases_seller_staff_user_id'), table_name=CASES)
    op.drop_table(CASES)
