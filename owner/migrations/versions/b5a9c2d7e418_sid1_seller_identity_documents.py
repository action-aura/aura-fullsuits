"""sid1_seller_identity_documents (the seller's own ID photo, 2026-11-01)

Revision ID: b5a9c2d7e418
Revises: a8f2c5d9e317
Create Date: 2026-11-01 09:00:00.000000

A seller must have a photo of their own ID on file before they can use Aura Owner. The
photo is an AES-256-GCM encrypted blob on disk (same code and keys as a customer's ID);
this table holds where it is, what it is, its SHA-256 and the key that encrypted it.

  one CURRENT photo per seller   partial unique index on staff_user_id WHERE superseded_at IS NULL
  one per open invitation        partial unique index on invitation_id WHERE superseded_at IS NULL
                                 AND staff_user_id IS NULL (a manager attaches the photo when
                                 creating the seller, before the StaffUser row exists; it is bound
                                 to the staff user at acceptance)
  size > 0, and an owner         CHECKs

Purely additive: a new table. Old code running against the new schema is unaffected.
DOWNGRADE refuses while any row exists, because that is identity evidence (and the
encrypted files on disk would be orphaned).

NOTE FOR WHOEVER CHAINS THIS ON: `down_revision` below is the only line that decides
where this migration sits. Re-point it when another migration lands first.
"""
from alembic import op
import sqlalchemy as sa


# --- the two lines to re-chain -------------------------------------------------
revision = 'b5a9c2d7e418'  # pragma: allowlist secret -- an Alembic revision id, not a credential
# ---
down_revision = 'a8f2c5d9e317'  # pragma: allowlist secret -- an Alembic revision id, not a credential
# -------------------------------------------------------------------------------
branch_labels = None
depends_on = None

TABLE = 'owner_staff_identity_documents'


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column('staff_user_id', sa.UUID(), nullable=True),
        sa.Column('invitation_id', sa.UUID(), nullable=True),
        sa.Column('storage_key', sa.String(length=255), nullable=False),
        sa.Column('content_type', sa.String(length=64), nullable=False),
        sa.Column('size_bytes', sa.BigInteger(), nullable=False),
        sa.Column('sha256', sa.String(length=64), nullable=False),
        sa.Column('enc_key_id', sa.String(length=32), nullable=False),
        sa.Column('uploaded_by_staff_user_id', sa.UUID(), nullable=True),
        sa.Column('uploaded_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('superseded_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint('size_bytes > 0', name='ck_owner_staff_identity_documents_size_positive'),
        sa.CheckConstraint(
            'staff_user_id IS NOT NULL OR invitation_id IS NOT NULL', name='ck_owner_staff_identity_documents_has_owner'),
        sa.ForeignKeyConstraint(['staff_user_id'], ['owner_staff_users.id']),
        sa.ForeignKeyConstraint(['invitation_id'], ['owner_staff_invitations.id']),
        sa.ForeignKeyConstraint(['uploaded_by_staff_user_id'], ['owner_staff_users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('storage_key'),
    )
    op.create_index(op.f('ix_owner_staff_identity_documents_staff_user_id'), TABLE, ['staff_user_id'], unique=False)
    op.create_index(op.f('ix_owner_staff_identity_documents_invitation_id'), TABLE, ['invitation_id'], unique=False)
    op.create_index(
        'uq_owner_staff_identity_documents_current_staff', TABLE, ['staff_user_id'], unique=True,
        postgresql_where=sa.text('superseded_at IS NULL AND staff_user_id IS NOT NULL'))
    op.create_index(
        'uq_owner_staff_identity_documents_current_invitation', TABLE, ['invitation_id'], unique=True,
        postgresql_where=sa.text('superseded_at IS NULL AND staff_user_id IS NULL'))


def downgrade() -> None:
    bind = op.get_bind()
    if bind.execute(sa.text(f'SELECT 1 FROM {TABLE} LIMIT 1')).first() is not None:
        raise RuntimeError(
            'Refusing to downgrade b5a9c2d7e418: owner_staff_identity_documents holds sellers\' ID photo records '
            '(identity evidence; the encrypted files would be orphaned). Export and delete them first.')
    op.drop_index('uq_owner_staff_identity_documents_current_invitation', table_name=TABLE)
    op.drop_index('uq_owner_staff_identity_documents_current_staff', table_name=TABLE)
    op.drop_index(op.f('ix_owner_staff_identity_documents_invitation_id'), table_name=TABLE)
    op.drop_index(op.f('ix_owner_staff_identity_documents_staff_user_id'), table_name=TABLE)
    op.drop_table(TABLE)
