"""ca3_customer_activation_grants (new customer flow, slice S5, 2026-10-31)

Revision ID: b1d4f8a26c93
Revises: f6d19b3a05c8
Create Date: 2026-10-31 17:00:00.000000

owner_customer_activation_grants: the two short-lived "permission slips" an app can
present at POST /api/licensing/v1/activations instead of a licence key:

  ACCOUNT_TICKET   made by POST /account-sign-ins (email + password, signed by the
                   device key). Bound to that key's fingerprint, 10 minutes, single
                   use, dead when the account's password changes
                   (session_version_at_issue stops matching).
  ACTIVATION_CODE  made by support / a super admin for ONE licence ("Make activation
                   code"). Stored as an HMAC under the licence pepper. 72 hours,
                   single use.

A CHECK makes a half-filled row of either kind impossible. Additive only: a new table
nothing in the old code reads, so it is safe with the previous release still running.

DOWNGRADE REFUSES while any grant row exists: a ticket/code row is the only record
that an activation was authorised by an account or a code instead of a key.

b1d4f8a26c93 was claimed in ROADMAP.md before this file was written (single-writer
resource), together with c5a2e7d94f16 and a3c7e9f15b28.
"""
from alembic import op
import sqlalchemy as sa


revision = 'b1d4f8a26c93'  # pragma: allowlist secret -- an Alembic revision id, not a credential
down_revision = 'f6d19b3a05c8'  # pragma: allowlist secret -- an Alembic revision id, not a credential
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'owner_customer_activation_grants',
        sa.Column('kind', sa.String(length=16), nullable=False),
        sa.Column('token_hash', sa.String(length=128), nullable=False),
        sa.Column('customer_id', sa.UUID(), nullable=False),
        sa.Column('account_id', sa.UUID(), nullable=True),
        sa.Column('license_id', sa.UUID(), nullable=True),
        sa.Column('device_key_fingerprint', sa.String(length=64), nullable=True),
        sa.Column('session_version_at_issue', sa.Integer(), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('consumed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_by_staff_user_id', sa.UUID(), nullable=True),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("kind IN ('ACCOUNT_TICKET', 'ACTIVATION_CODE')", name='ck_owner_customer_activation_grants_kind'),
        sa.CheckConstraint(
            "(kind = 'ACCOUNT_TICKET' AND account_id IS NOT NULL AND device_key_fingerprint IS NOT NULL "
            "AND session_version_at_issue IS NOT NULL AND license_id IS NULL) OR "
            "(kind = 'ACTIVATION_CODE' AND license_id IS NOT NULL AND account_id IS NULL "
            "AND device_key_fingerprint IS NULL AND session_version_at_issue IS NULL)",
            name='ck_owner_customer_activation_grants_shape'),
        sa.ForeignKeyConstraint(['account_id'], ['owner_customer_accounts.id']),
        sa.ForeignKeyConstraint(['created_by_staff_user_id'], ['owner_staff_users.id']),
        sa.ForeignKeyConstraint(['customer_id'], ['owner_customers.id']),
        sa.ForeignKeyConstraint(['license_id'], ['owner_licenses.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('token_hash'),
    )
    op.create_index('ix_owner_customer_activation_grants_customer_id', 'owner_customer_activation_grants', ['customer_id'], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.execute(sa.text('SELECT 1 FROM owner_customer_activation_grants LIMIT 1')).first() is not None:
        raise RuntimeError(
            'Refusing to downgrade b1d4f8a26c93: owner_customer_activation_grants still holds rows, the only record '
            'that activations were authorised by an account ticket or an activation code. Export or remove them '
            'deliberately first.'
        )
    op.drop_index('ix_owner_customer_activation_grants_customer_id', table_name='owner_customer_activation_grants')
    op.drop_table('owner_customer_activation_grants')
