"""owner_email_outbox: Owner sends email (staff invitations + the product mail relay)

Revision ID: b5e1c8a27d94
Revises: a3d9e6c14b72
Create Date: 2026-10-10 12:00:00.000000

New table only -- nothing existing is altered, so it is safe to apply with the
old code still running (the old code never touches it) and safe to roll back
(downgrade drops only this table).

The two indexes serve the worker's claim query (status, next_attempt_at) and
the relay's per-installation caps (installation_id, created_at).

a3d9e6c14b72 was named as this wave's down_revision in ROADMAP.md before work
started (single-writer resource: exactly one new head on top of it).
"""
from alembic import op
import sqlalchemy as sa


revision = 'b5e1c8a27d94'
down_revision = 'a3d9e6c14b72'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('owner_email_outbox',
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('to_address', sa.String(length=320), nullable=False),
    sa.Column('subject', sa.String(length=300), nullable=False),
    sa.Column('body_text', sa.Text(), nullable=False),
    sa.Column('body_html', sa.Text(), nullable=True),
    sa.Column('status', sa.String(length=24), nullable=False),
    sa.Column('attempt_count', sa.Integer(), nullable=False),
    sa.Column('next_attempt_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('lease_expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_error', sa.String(length=120), nullable=True),
    sa.Column('source', sa.String(length=16), nullable=False),
    sa.Column('installation_id', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.ForeignKeyConstraint(['installation_id'], ['owner_installations.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_owner_email_outbox_status_next_attempt', 'owner_email_outbox', ['status', 'next_attempt_at'], unique=False)
    op.create_index('ix_owner_email_outbox_installation_created', 'owner_email_outbox', ['installation_id', 'created_at'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_owner_email_outbox_installation_created', table_name='owner_email_outbox')
    op.drop_index('ix_owner_email_outbox_status_next_attempt', table_name='owner_email_outbox')
    op.drop_table('owner_email_outbox')
