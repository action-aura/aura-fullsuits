"""nc1_customer_onboarding_trial (new customer flow, slice S2, 2026-10-31)

Revision ID: e2c4a8b1f730
Revises: c7d41e90b2a5
Create Date: 2026-10-31 12:00:00.000000

The seller's "Create a new customer" form makes a customer, an ACTIVE website
account with a temporary password, two ID photos and a 7-day trial in ONE
action. What that needs from the database:

  owner_customer_accounts
      + password_must_change boolean NOT NULL default false   (temp password:
        the customer must choose their own at the first sign-in)
      + email_verified_at timestamptz NULL                    (a seller-typed
        address is not verified until the customer uses the VERIFY_EMAIL link)
      + password_set_by_staff_user_id uuid NULL FK            (who typed it)
  owner_customer_account_tokens
      kind CHECK widened to include VERIFY_EMAIL
  owner_customer_documents (new)
      the encrypted ID / commercial-registration photos (and, in the next
      revision's feature, payment slips). Rows only; the bytes live on disk.
  owner_trial_device_marks (new)
      one row per physical device that has started a trial; UNIQUE on the
      device-key fingerprint is "one trial per device, ever".
  owner_pilot_records
      + pilot_kind (PILOT | TRIAL) NOT NULL default 'PILOT', + trial_started_at,
      + trial_length_days, and a PARTIAL unique index: one TRIAL per customer.

Safe with the old code running: every added column is nullable or has a
default, the new tables are not read by it, and existing rows become
pilot_kind='PILOT' / password_must_change=false (exactly what they were).

DOWNGRADE REFUSES while it would delete data: any document, device mark, TRIAL
pilot, account flagged password_must_change / email_verified, or VERIFY_EMAIL
token. Empty them deliberately first -- a rollback must not silently shred a
customer's ID photos or restart their trial.

e2c4a8b1f730 was claimed in ROADMAP.md before this file was written
(single-writer resource).
"""
from alembic import op
import sqlalchemy as sa


revision = 'e2c4a8b1f730'
down_revision = 'c7d41e90b2a5'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('owner_customer_accounts', sa.Column(
        'password_must_change', sa.Boolean(), server_default=sa.text('false'), nullable=False))
    op.add_column('owner_customer_accounts', sa.Column('email_verified_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('owner_customer_accounts', sa.Column('password_set_by_staff_user_id', sa.UUID(), nullable=True))
    op.create_foreign_key(
        'fk_owner_customer_accounts_password_set_by', 'owner_customer_accounts', 'owner_staff_users',
        ['password_set_by_staff_user_id'], ['id'])

    op.drop_constraint('ck_owner_customer_account_tokens_kind', 'owner_customer_account_tokens', type_='check')
    op.create_check_constraint(
        'ck_owner_customer_account_tokens_kind', 'owner_customer_account_tokens',
        "kind IN ('INVITE', 'RESET', 'EMAIL_CHANGE', 'VERIFY_EMAIL')")

    op.create_table(
        'owner_customer_documents',
        sa.Column('customer_id', sa.UUID(), nullable=False),
        sa.Column('kind', sa.String(length=32), nullable=False),
        sa.Column('payment_record_id', sa.UUID(), nullable=True),
        sa.Column('storage_key', sa.String(length=255), nullable=False),
        sa.Column('content_type', sa.String(length=64), nullable=False),
        sa.Column('size_bytes', sa.BigInteger(), nullable=False),
        sa.Column('sha256', sa.String(length=64), nullable=False),
        sa.Column('enc_key_id', sa.String(length=32), nullable=False),
        sa.Column('uploaded_by_staff_user_id', sa.UUID(), nullable=True),
        sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('purge_after', sa.DateTime(timezone=True), nullable=True),
        sa.Column('purged_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.CheckConstraint(
            "kind IN ('NATIONAL_ID', 'COMMERCIAL_REGISTRATION', 'PAYMENT_SLIP')", name='ck_owner_customer_documents_kind'),
        sa.CheckConstraint(
            "(kind = 'PAYMENT_SLIP' AND payment_record_id IS NOT NULL) OR "
            "(kind <> 'PAYMENT_SLIP' AND payment_record_id IS NULL)",
            name='ck_owner_customer_documents_slip_has_payment'),
        sa.CheckConstraint('size_bytes > 0', name='ck_owner_customer_documents_size_positive'),
        sa.ForeignKeyConstraint(['customer_id'], ['owner_customers.id']),
        sa.ForeignKeyConstraint(['payment_record_id'], ['owner_payment_records.id']),
        sa.ForeignKeyConstraint(['uploaded_by_staff_user_id'], ['owner_staff_users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('storage_key', name='uq_owner_customer_documents_storage_key'),
    )
    op.create_index('ix_owner_customer_documents_customer_id', 'owner_customer_documents', ['customer_id'], unique=False)
    op.create_index('ix_owner_customer_documents_payment_record_id', 'owner_customer_documents', ['payment_record_id'], unique=False)

    op.create_table(
        'owner_trial_device_marks',
        sa.Column('device_key_fingerprint', sa.String(length=128), nullable=False),
        sa.Column('registry_device_id', sa.String(length=64), nullable=True),
        sa.Column('customer_id', sa.UUID(), nullable=False),
        sa.Column('installation_id', sa.UUID(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(['customer_id'], ['owner_customers.id']),
        sa.ForeignKeyConstraint(['installation_id'], ['owner_installations.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('device_key_fingerprint', name='uq_owner_trial_device_marks_device_key_fingerprint'),
    )
    op.create_index('ix_owner_trial_device_marks_customer_id', 'owner_trial_device_marks', ['customer_id'], unique=False)

    op.add_column('owner_pilot_records', sa.Column(
        'pilot_kind', sa.String(length=8), server_default='PILOT', nullable=False))
    op.add_column('owner_pilot_records', sa.Column('trial_started_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('owner_pilot_records', sa.Column('trial_length_days', sa.Integer(), nullable=True))
    op.create_check_constraint('ck_owner_pilot_records_pilot_kind', 'owner_pilot_records', "pilot_kind IN ('PILOT', 'TRIAL')")
    op.create_index(
        'uq_owner_pilot_records_one_trial_per_customer', 'owner_pilot_records', ['customer_id'], unique=True,
        postgresql_where=sa.text("pilot_kind = 'TRIAL'"))


def downgrade() -> None:
    bind = op.get_bind()
    holding = []
    for label, sql in (
        ('owner_customer_documents', 'SELECT 1 FROM owner_customer_documents LIMIT 1'),
        ('owner_trial_device_marks', 'SELECT 1 FROM owner_trial_device_marks LIMIT 1'),
        ('TRIAL pilot records', "SELECT 1 FROM owner_pilot_records WHERE pilot_kind = 'TRIAL' LIMIT 1"),
        ('accounts with password_must_change / email_verified_at / password_set_by',
         'SELECT 1 FROM owner_customer_accounts WHERE password_must_change OR email_verified_at IS NOT NULL '
         'OR password_set_by_staff_user_id IS NOT NULL LIMIT 1'),
        ('VERIFY_EMAIL tokens', "SELECT 1 FROM owner_customer_account_tokens WHERE kind = 'VERIFY_EMAIL' LIMIT 1"),
    ):
        if bind.execute(sa.text(sql)).first() is not None:
            holding.append(label)
    if holding:
        raise RuntimeError(
            'Refusing to downgrade e2c4a8b1f730: these still hold data a rollback would delete: '
            + ', '.join(holding) + '. Empty them deliberately first.'
        )
    op.drop_index('uq_owner_pilot_records_one_trial_per_customer', table_name='owner_pilot_records')
    op.drop_constraint('ck_owner_pilot_records_pilot_kind', 'owner_pilot_records', type_='check')
    op.drop_column('owner_pilot_records', 'trial_length_days')
    op.drop_column('owner_pilot_records', 'trial_started_at')
    op.drop_column('owner_pilot_records', 'pilot_kind')
    op.drop_index('ix_owner_trial_device_marks_customer_id', table_name='owner_trial_device_marks')
    op.drop_table('owner_trial_device_marks')
    op.drop_index('ix_owner_customer_documents_payment_record_id', table_name='owner_customer_documents')
    op.drop_index('ix_owner_customer_documents_customer_id', table_name='owner_customer_documents')
    op.drop_table('owner_customer_documents')
    op.drop_constraint('ck_owner_customer_account_tokens_kind', 'owner_customer_account_tokens', type_='check')
    op.create_check_constraint(
        'ck_owner_customer_account_tokens_kind', 'owner_customer_account_tokens', "kind IN ('INVITE', 'RESET', 'EMAIL_CHANGE')")
    op.drop_constraint('fk_owner_customer_accounts_password_set_by', 'owner_customer_accounts', type_='foreignkey')
    op.drop_column('owner_customer_accounts', 'password_set_by_staff_user_id')
    op.drop_column('owner_customer_accounts', 'email_verified_at')
    op.drop_column('owner_customer_accounts', 'password_must_change')
