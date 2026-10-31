"""step2_ca1_customer_website_accounts (Step 2, slice CA1, 2026-10-25)

Revision ID: a6e81bf3d32c
Revises: d4b8f1a62e07
Create Date: 2026-10-25 20:00:00.000000

A customer's account on the Action Aura website lives in Owner, tied to the
customer record staff already use (app/models/customer_accounts.py has the
state machine). Four NEW tables and one new nullable column; nothing existing
is altered except that column:

  owner_customer_accounts         one per customer (unique), one email = one
                                  company (unique), INVITED/ACTIVE/DISABLED
  owner_customer_account_tokens   INVITE 72h / RESET 30min / EMAIL_CHANGE 24h,
                                  hash only, single use
  owner_customer_sessions         idle 60min / absolute 12h, session_version
  owner_customer_login_attempts   hashed email / IP, SEPARATE from the staff
                                  owner_login_attempts
  owner_subscriptions.branch_label varchar(120) NULL -- the shop's name in the
                                  customer's own branch picker

Safe with the old code running: it never reads or writes the new tables, and a
nullable column with no default is invisible to it. CHECKs are spelled
IS [NOT] NULL, never "col = NULL".

DOWNGRADE REFUSES while any account, token, session, attempt or branch label
exists: dropping them would silently delete customers' sign-ins (and their
password hashes), which is not something a rollback may do by accident. Empty
them deliberately first.

a6e81bf3d32c was claimed in ROADMAP.md before this migration was written
(single-writer resource), commit 8b5d1603.

RE-PARENTED 2026-10-31 (seller-launch S0): it was written as a child of
a29865d5eab4, but release/seller-launch-owner already carries d4b8f1a62e07
(keep-selling release_kind) on top of a29865d5eab4, so merging as-is gave two
heads. Nothing in this migration depends on that column; it only needed a
single linear chain. Claimed in ROADMAP.md first.
"""
from alembic import op
import sqlalchemy as sa


revision = 'a6e81bf3d32c'
down_revision = 'd4b8f1a62e07'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'owner_customer_accounts',
        sa.Column('customer_id', sa.UUID(), nullable=False),
        sa.Column('email', sa.String(length=255), nullable=False),
        sa.Column('password_hash', sa.String(length=255), nullable=True),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('language', sa.String(length=8), nullable=False),
        sa.Column('session_version', sa.Integer(), nullable=False),
        sa.Column('password_changed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('activated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_sign_in_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('disabled_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('disabled_reason', sa.Text(), nullable=True),
        sa.Column('disabled_by_staff_user_id', sa.UUID(), nullable=True),
        sa.Column('created_by_staff_user_id', sa.UUID(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.CheckConstraint("status IN ('INVITED', 'ACTIVE', 'DISABLED')", name='ck_owner_customer_accounts_status'),
        sa.CheckConstraint("language IN ('en', 'ar')", name='ck_owner_customer_accounts_language'),
        sa.CheckConstraint(
            "status <> 'ACTIVE' OR password_hash IS NOT NULL", name='ck_owner_customer_accounts_active_has_password'
        ),
        sa.CheckConstraint(
            "(status = 'DISABLED' AND disabled_at IS NOT NULL) OR (status <> 'DISABLED' AND disabled_at IS NULL)",
            name='ck_owner_customer_accounts_disabled_at',
        ),
        sa.ForeignKeyConstraint(['customer_id'], ['owner_customers.id']),
        sa.ForeignKeyConstraint(['disabled_by_staff_user_id'], ['owner_staff_users.id']),
        sa.ForeignKeyConstraint(['created_by_staff_user_id'], ['owner_staff_users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('customer_id', name='uq_owner_customer_accounts_customer_id'),
        sa.UniqueConstraint('email', name='uq_owner_customer_accounts_email'),
    )

    op.create_table(
        'owner_customer_account_tokens',
        sa.Column('account_id', sa.UUID(), nullable=False),
        sa.Column('kind', sa.String(length=16), nullable=False),
        sa.Column('token_hash', sa.String(length=128), nullable=False),
        sa.Column('new_email', sa.String(length=255), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('consumed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_by_staff_user_id', sa.UUID(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.CheckConstraint("kind IN ('INVITE', 'RESET', 'EMAIL_CHANGE')", name='ck_owner_customer_account_tokens_kind'),
        sa.CheckConstraint(
            "(kind = 'EMAIL_CHANGE' AND new_email IS NOT NULL) OR (kind <> 'EMAIL_CHANGE' AND new_email IS NULL)",
            name='ck_owner_customer_account_tokens_new_email',
        ),
        sa.ForeignKeyConstraint(['account_id'], ['owner_customer_accounts.id']),
        sa.ForeignKeyConstraint(['created_by_staff_user_id'], ['owner_staff_users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('token_hash', name='uq_owner_customer_account_tokens_token_hash'),
    )
    op.create_index('ix_owner_customer_account_tokens_account_id', 'owner_customer_account_tokens', ['account_id'], unique=False)

    op.create_table(
        'owner_customer_sessions',
        sa.Column('account_id', sa.UUID(), nullable=False),
        sa.Column('token_hash', sa.String(length=128), nullable=False),
        sa.Column('session_version_at_login', sa.Integer(), nullable=False),
        sa.Column('ip_address', sa.String(length=64), nullable=True),
        sa.Column('user_agent', sa.String(length=512), nullable=True),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('absolute_expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('revoked_reason', sa.String(length=64), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(['account_id'], ['owner_customer_accounts.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('token_hash', name='uq_owner_customer_sessions_token_hash'),
    )
    op.create_index('ix_owner_customer_sessions_account_id', 'owner_customer_sessions', ['account_id'], unique=False)

    op.create_table(
        'owner_customer_login_attempts',
        sa.Column('email_hash', sa.String(length=64), nullable=False),
        sa.Column('ip_hash', sa.String(length=64), nullable=True),
        sa.Column('success', sa.Boolean(), nullable=False),
        sa.Column('reason', sa.String(length=64), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_owner_customer_login_attempts_email_hash', 'owner_customer_login_attempts', ['email_hash'], unique=False)

    op.add_column('owner_subscriptions', sa.Column('branch_label', sa.String(length=120), nullable=True))


_REFUSAL_TABLES = (
    'owner_customer_accounts',
    'owner_customer_account_tokens',
    'owner_customer_sessions',
    'owner_customer_login_attempts',
)


def downgrade() -> None:
    bind = op.get_bind()
    holding = []
    for table in _REFUSAL_TABLES:
        if bind.execute(sa.text(f'SELECT 1 FROM {table} LIMIT 1')).first() is not None:
            holding.append(table)
    if bind.execute(sa.text('SELECT 1 FROM owner_subscriptions WHERE branch_label IS NOT NULL LIMIT 1')).first() is not None:
        holding.append('owner_subscriptions.branch_label')
    if holding:
        raise RuntimeError(
            'Refusing to downgrade a6e81bf3d32c: these still hold customer data that a rollback would delete: '
            + ', '.join(holding)
            + '. Empty them deliberately first.'
        )
    op.drop_column('owner_subscriptions', 'branch_label')
    op.drop_index('ix_owner_customer_login_attempts_email_hash', table_name='owner_customer_login_attempts')
    op.drop_table('owner_customer_login_attempts')
    op.drop_index('ix_owner_customer_sessions_account_id', table_name='owner_customer_sessions')
    op.drop_table('owner_customer_sessions')
    op.drop_index('ix_owner_customer_account_tokens_account_id', table_name='owner_customer_account_tokens')
    op.drop_table('owner_customer_account_tokens')
    op.drop_table('owner_customer_accounts')
