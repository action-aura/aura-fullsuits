"""ca2_website_download_links (new customer flow, slice S4, 2026-10-31)

Revision ID: a3c7e9f15b28
Revises: c5a2e7d94f16
Create Date: 2026-10-31 18:00:00.000000

A signed-in customer downloads the installer from the website BEFORE any device
exists. The existing download authorization (owner_release_download_authorizations)
was built for an installed device (installation_id + license_id, both NOT NULL, one
use). This revision lets the same table also hold a customer's link:

  customer_account_id uuid NULL FK owner_customer_accounts
  installation_id, license_id -> NULL allowed
  CHECK num_nonnulls(installation_id, customer_account_id) = 1
        -- a link belongs to a device OR to a website account, never both, never
        -- neither, so one kind can never be redeemed as the other.
  CHECK installation_id IS NULL OR license_id IS NOT NULL
        -- a device link keeps the licence it was issued under, as before.

Safe with the old code running: every existing row has installation_id set and the new
column NULL, so both CHECKs hold for all of them.

DOWNGRADE REFUSES while any customer link exists (it could not satisfy the old NOT NULL
columns); delete or let them expire (10 minutes) first.

a3c7e9f15b28 was claimed in ROADMAP.md before this file was written (single-writer
resource), together with b1d4f8a26c93 and c5a2e7d94f16.
"""
from alembic import op
import sqlalchemy as sa


revision = 'a3c7e9f15b28'  # pragma: allowlist secret -- an Alembic revision id, not a credential
down_revision = 'c5a2e7d94f16'  # pragma: allowlist secret -- an Alembic revision id, not a credential
branch_labels = None
depends_on = None

TABLE = 'owner_release_download_authorizations'


def upgrade() -> None:
    op.add_column(TABLE, sa.Column('customer_account_id', sa.UUID(), nullable=True))
    op.create_foreign_key(
        'fk_owner_release_download_authorizations_customer_account', TABLE, 'owner_customer_accounts',
        ['customer_account_id'], ['id'])
    op.create_index('ix_owner_release_download_authorizations_customer_account_id', TABLE, ['customer_account_id'], unique=False)
    op.alter_column(TABLE, 'license_id', existing_type=sa.UUID(), nullable=True)
    op.alter_column(TABLE, 'installation_id', existing_type=sa.UUID(), nullable=True)
    op.create_check_constraint(
        'ck_owner_release_download_authorizations_one_owner', TABLE, 'num_nonnulls(installation_id, customer_account_id) = 1')
    op.create_check_constraint(
        'ck_owner_release_download_authorizations_device_has_license', TABLE, 'installation_id IS NULL OR license_id IS NOT NULL')


def downgrade() -> None:
    bind = op.get_bind()
    if bind.execute(sa.text(f'SELECT 1 FROM {TABLE} WHERE customer_account_id IS NOT NULL LIMIT 1')).first() is not None:
        raise RuntimeError(
            'Refusing to downgrade a3c7e9f15b28: website download links still exist and cannot satisfy the old NOT NULL '
            'device columns. Delete them (they expire in 10 minutes) first.'
        )
    op.drop_constraint('ck_owner_release_download_authorizations_device_has_license', TABLE, type_='check')
    op.drop_constraint('ck_owner_release_download_authorizations_one_owner', TABLE, type_='check')
    op.alter_column(TABLE, 'installation_id', existing_type=sa.UUID(), nullable=False)
    op.alter_column(TABLE, 'license_id', existing_type=sa.UUID(), nullable=False)
    op.drop_index('ix_owner_release_download_authorizations_customer_account_id', table_name=TABLE)
    op.drop_constraint('fk_owner_release_download_authorizations_customer_account', TABLE, type_='foreignkey')
    op.drop_column(TABLE, 'customer_account_id')
