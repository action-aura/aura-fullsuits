"""cn1_customer_notices (contract copy mail, separate consents, retention, 2026-11-01)

Revision ID: d7c3f1a9e254
Revises: d4f8b2a6c137
Create Date: 2026-11-01 14:00:00.000000

The product changes the v2 shop agreement / privacy notice depend on (legal review
section (d)). All additive; old code running against this schema is unaffected.

  owner_email_outbox.dedupe_key      one mail per EVENT (unique where not null): a
                                     contract acceptance, a payment status. Existing
                                     rows keep NULL.
  owner_contract_consents            the customer's two separate consents, APPEND-ONLY
                                     (trigger), one row per kind per acceptance.
  owner_customers.manual_id_verification_required_at
                                     set when the customer refused to let us keep their ID
                                     photos (the photos are deleted; staff verify by hand).
  owner_trial_device_marks.customer_id
                                     becomes NULLABLE: when a customer's data is purged the
                                     device fingerprint stays (fraud prevention) and only
                                     the link to the person is dropped.
  owner_staff_identity_documents.purge_after / purged_at
                                     the seller ID photo's retention (365 days after the
                                     seller is disabled).

NOTE FOR WHOEVER CHAINS THIS ON: `down_revision` below is the only line that decides
where this migration sits. This was re-chained at merge onto d4f8b2a6c137 (customer ownership),
the head of release/seller-launch-owner.

DOWNGRADE refuses while any consent row exists (consent evidence) and while any trial
device mark has no customer (NOT NULL could not be restored without inventing a person).
"""
from alembic import op
import sqlalchemy as sa


# --- the two lines to re-chain (re-chain at merge) --------------------------------
revision = 'd7c3f1a9e254'  # pragma: allowlist secret -- an Alembic revision id, not a credential
# re-chained at merge onto the cash hand-in / customer ownership head
down_revision = 'd4f8b2a6c137'  # pragma: allowlist secret -- an Alembic revision id, not a credential
# -------------------------------------------------------------------------------
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('owner_email_outbox', sa.Column('dedupe_key', sa.String(length=200), nullable=True))
    op.create_index(
        'uq_owner_email_outbox_dedupe_key', 'owner_email_outbox', ['dedupe_key'], unique=True,
        postgresql_where=sa.text('dedupe_key IS NOT NULL'))

    op.create_table(
        'owner_contract_consents',
        sa.Column('acceptance_id', sa.UUID(), nullable=False),
        sa.Column('customer_id', sa.UUID(), nullable=False),
        sa.Column('kind', sa.String(length=32), nullable=False),
        sa.Column('given', sa.Boolean(), nullable=False),
        sa.Column('given_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('installation_id', sa.String(length=128), nullable=True),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("kind IN ('id_documents', 'data_outside_jordan')", name='ck_owner_contract_consents_kind'),
        sa.ForeignKeyConstraint(['acceptance_id'], ['owner_contract_acceptances.id']),
        sa.ForeignKeyConstraint(['customer_id'], ['owner_customers.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('acceptance_id', 'kind', name='uq_owner_contract_consents_acceptance_kind'),
    )
    op.create_index(op.f('ix_owner_contract_consents_acceptance_id'), 'owner_contract_consents', ['acceptance_id'], unique=False)
    op.create_index(op.f('ix_owner_contract_consents_customer_id'), 'owner_contract_consents', ['customer_id'], unique=False)
    op.execute("""
        CREATE FUNCTION owner_contract_consents_append_only() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'owner_contract_consents is append-only (% refused)', TG_OP;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER trg_owner_contract_consents_append_only
        BEFORE UPDATE OR DELETE ON owner_contract_consents
        FOR EACH ROW EXECUTE FUNCTION owner_contract_consents_append_only()
    """)

    op.add_column(
        'owner_customers', sa.Column('manual_id_verification_required_at', sa.DateTime(timezone=True), nullable=True))
    op.alter_column('owner_trial_device_marks', 'customer_id', existing_type=sa.UUID(), nullable=True)
    op.add_column('owner_staff_identity_documents', sa.Column('purge_after', sa.DateTime(timezone=True), nullable=True))
    op.add_column('owner_staff_identity_documents', sa.Column('purged_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if bind.execute(sa.text('SELECT 1 FROM owner_contract_consents LIMIT 1')).first() is not None:
        raise RuntimeError(
            'Refusing to downgrade d7c3f1a9e254: owner_contract_consents holds consent evidence. '
            'Export and delete it first.')
    if bind.execute(sa.text('SELECT 1 FROM owner_trial_device_marks WHERE customer_id IS NULL LIMIT 1')).first() is not None:
        raise RuntimeError(
            'Refusing to downgrade d7c3f1a9e254: owner_trial_device_marks has rows whose customer was purged '
            '(NOT NULL cannot be restored without inventing a customer).')
    op.drop_column('owner_staff_identity_documents', 'purged_at')
    op.drop_column('owner_staff_identity_documents', 'purge_after')
    op.alter_column('owner_trial_device_marks', 'customer_id', existing_type=sa.UUID(), nullable=False)
    op.drop_column('owner_customers', 'manual_id_verification_required_at')
    op.execute('DROP TRIGGER trg_owner_contract_consents_append_only ON owner_contract_consents')
    op.execute('DROP FUNCTION owner_contract_consents_append_only()')
    op.drop_table('owner_contract_consents')
    op.drop_index('uq_owner_email_outbox_dedupe_key', table_name='owner_email_outbox')
    op.drop_column('owner_email_outbox', 'dedupe_key')
