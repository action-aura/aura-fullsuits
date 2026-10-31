"""ct1_contract_versions_and_acceptances (new customer flow, slice S5, 2026-10-31)

Revision ID: c5a2e7d94f16
Revises: b1d4f8a26c93
Create Date: 2026-10-31 17:30:00.000000

The e-contract, for two audiences (CUSTOMER: the shop owner at first sign-in in the
app; SELLER: a staff member who sells).

  owner_contract_versions      the text. At most ONE live (published, not retired)
      version per audience + language (partial unique index). body_sha256 is CHECKed
      by the database against body, and a trigger refuses any change of body, hash,
      audience or language after the row exists: an old acceptance must always point
      at exactly the words that were shown, so a typo fix is a NEW version.
  owner_contract_acceptances   the evidence. APPEND-ONLY: a trigger refuses UPDATE
      and DELETE. One per customer per version, one per staff member per version
      (partial unique indexes); a CHECK ties the audience to who is named.

Additive only (two new tables + two trigger functions nothing else uses).

DOWNGRADE REFUSES while either table has rows: acceptances are legal evidence and the
versions are what they point at.

c5a2e7d94f16 was claimed in ROADMAP.md before this file was written (single-writer
resource), together with b1d4f8a26c93 and a3c7e9f15b28.
"""
from alembic import op
import sqlalchemy as sa


revision = 'c5a2e7d94f16'  # pragma: allowlist secret -- an Alembic revision id, not a credential
down_revision = 'b1d4f8a26c93'  # pragma: allowlist secret -- an Alembic revision id, not a credential
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'owner_contract_versions',
        sa.Column('audience', sa.String(length=16), nullable=False),
        sa.Column('language', sa.String(length=8), nullable=False),
        sa.Column('version_label', sa.String(length=64), nullable=False),
        sa.Column('title', sa.String(length=255), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('body_sha256', sa.String(length=64), nullable=False),
        sa.Column('pdf_storage_key', sa.String(length=255), nullable=True),
        sa.Column('pdf_sha256', sa.String(length=64), nullable=True),
        sa.Column('pdf_size_bytes', sa.BigInteger(), nullable=True),
        sa.Column('pdf_enc_key_id', sa.String(length=32), nullable=True),
        sa.Column('lawyer_approved', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('lawyer_approval_note', sa.Text(), nullable=True),
        sa.Column('lawyer_approved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('lawyer_approved_by_staff_user_id', sa.UUID(), nullable=True),
        sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('published_by_staff_user_id', sa.UUID(), nullable=True),
        sa.Column('retired_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_by_staff_user_id', sa.UUID(), nullable=True),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("audience IN ('CUSTOMER', 'SELLER')", name='ck_owner_contract_versions_audience'),
        sa.CheckConstraint("language IN ('en', 'ar')", name='ck_owner_contract_versions_language'),
        sa.CheckConstraint("body_sha256 = encode(sha256(convert_to(body, 'UTF8')), 'hex')", name='ck_owner_contract_versions_body_sha256'),
        sa.CheckConstraint('retired_at IS NULL OR published_at IS NOT NULL', name='ck_owner_contract_versions_retired_was_published'),
        sa.CheckConstraint('(pdf_storage_key IS NULL) = (pdf_sha256 IS NULL)', name='ck_owner_contract_versions_pdf_all_or_nothing'),
        sa.ForeignKeyConstraint(['created_by_staff_user_id'], ['owner_staff_users.id']),
        sa.ForeignKeyConstraint(['lawyer_approved_by_staff_user_id'], ['owner_staff_users.id']),
        sa.ForeignKeyConstraint(['published_by_staff_user_id'], ['owner_staff_users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('audience', 'language', 'version_label', name='uq_owner_contract_versions_label'),
    )
    op.create_index(
        'uq_owner_contract_versions_one_live', 'owner_contract_versions', ['audience', 'language'], unique=True,
        postgresql_where=sa.text('published_at IS NOT NULL AND retired_at IS NULL'))

    op.create_table(
        'owner_contract_acceptances',
        sa.Column('contract_version_id', sa.UUID(), nullable=False),
        sa.Column('audience', sa.String(length=16), nullable=False),
        sa.Column('customer_id', sa.UUID(), nullable=True),
        sa.Column('account_id', sa.UUID(), nullable=True),
        sa.Column('staff_user_id', sa.UUID(), nullable=True),
        sa.Column('email_snapshot', sa.String(length=255), nullable=False),
        sa.Column('installation_id', sa.String(length=128), nullable=True),
        sa.Column('device_key_fingerprint', sa.String(length=64), nullable=True),
        sa.Column('device_signature', sa.Text(), nullable=True),
        sa.Column('ip', sa.String(length=64), nullable=True),
        sa.Column('app_version', sa.String(length=64), nullable=True),
        sa.Column('language', sa.String(length=8), nullable=False),
        sa.Column('text_sha256', sa.String(length=64), nullable=False),
        sa.Column('accepted_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("audience IN ('CUSTOMER', 'SELLER')", name='ck_owner_contract_acceptances_audience'),
        sa.CheckConstraint(
            "(audience = 'CUSTOMER' AND customer_id IS NOT NULL AND staff_user_id IS NULL) OR "
            "(audience = 'SELLER' AND staff_user_id IS NOT NULL AND customer_id IS NULL)",
            name='ck_owner_contract_acceptances_who'),
        sa.ForeignKeyConstraint(['account_id'], ['owner_customer_accounts.id']),
        sa.ForeignKeyConstraint(['contract_version_id'], ['owner_contract_versions.id']),
        sa.ForeignKeyConstraint(['customer_id'], ['owner_customers.id']),
        sa.ForeignKeyConstraint(['staff_user_id'], ['owner_staff_users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_owner_contract_acceptances_contract_version_id', 'owner_contract_acceptances', ['contract_version_id'], unique=False)
    op.create_index('ix_owner_contract_acceptances_customer_id', 'owner_contract_acceptances', ['customer_id'], unique=False)
    op.create_index('ix_owner_contract_acceptances_staff_user_id', 'owner_contract_acceptances', ['staff_user_id'], unique=False)
    op.create_index(
        'uq_owner_contract_acceptances_customer', 'owner_contract_acceptances', ['customer_id', 'contract_version_id'],
        unique=True, postgresql_where=sa.text('customer_id IS NOT NULL'))
    op.create_index(
        'uq_owner_contract_acceptances_staff', 'owner_contract_acceptances', ['staff_user_id', 'contract_version_id'],
        unique=True, postgresql_where=sa.text('staff_user_id IS NOT NULL'))

    # Append-only evidence and immutable text, enforced where no application bug can skip it.
    op.execute("""
        CREATE FUNCTION owner_contract_acceptances_append_only() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'owner_contract_acceptances is append-only (% refused)', TG_OP;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER trg_owner_contract_acceptances_append_only
        BEFORE UPDATE OR DELETE ON owner_contract_acceptances
        FOR EACH ROW EXECUTE FUNCTION owner_contract_acceptances_append_only()
    """)
    op.execute("""
        CREATE FUNCTION owner_contract_versions_text_is_immutable() RETURNS trigger AS $$
        BEGIN
            IF NEW.body IS DISTINCT FROM OLD.body OR NEW.body_sha256 IS DISTINCT FROM OLD.body_sha256
               OR NEW.audience IS DISTINCT FROM OLD.audience OR NEW.language IS DISTINCT FROM OLD.language THEN
                RAISE EXCEPTION 'a contract version text, hash, audience and language never change; create a new version';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER trg_owner_contract_versions_text_is_immutable
        BEFORE UPDATE ON owner_contract_versions
        FOR EACH ROW EXECUTE FUNCTION owner_contract_versions_text_is_immutable()
    """)


def downgrade() -> None:
    bind = op.get_bind()
    for table in ('owner_contract_acceptances', 'owner_contract_versions'):
        if bind.execute(sa.text(f'SELECT 1 FROM {table} LIMIT 1')).first() is not None:
            raise RuntimeError(
                f'Refusing to downgrade c5a2e7d94f16: {table} still holds rows. Acceptances are legal evidence; '
                'export them and remove them deliberately first.'
            )
    op.execute('DROP TRIGGER trg_owner_contract_versions_text_is_immutable ON owner_contract_versions')
    op.execute('DROP FUNCTION owner_contract_versions_text_is_immutable()')
    op.execute('DROP TRIGGER trg_owner_contract_acceptances_append_only ON owner_contract_acceptances')
    op.execute('DROP FUNCTION owner_contract_acceptances_append_only()')
    op.drop_index('uq_owner_contract_acceptances_staff', table_name='owner_contract_acceptances')
    op.drop_index('uq_owner_contract_acceptances_customer', table_name='owner_contract_acceptances')
    op.drop_index('ix_owner_contract_acceptances_staff_user_id', table_name='owner_contract_acceptances')
    op.drop_index('ix_owner_contract_acceptances_customer_id', table_name='owner_contract_acceptances')
    op.drop_index('ix_owner_contract_acceptances_contract_version_id', table_name='owner_contract_acceptances')
    op.drop_table('owner_contract_acceptances')
    op.drop_index('uq_owner_contract_versions_one_live', table_name='owner_contract_versions')
    op.drop_table('owner_contract_versions')
