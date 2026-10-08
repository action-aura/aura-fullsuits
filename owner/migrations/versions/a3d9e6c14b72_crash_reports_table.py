"""crash reports: owner_crash_reports (installed products report bugs to Owner)

Revision ID: a3d9e6c14b72
Revises: 7c41d2e9a0b5
Create Date: 2026-10-08 12:00:00.000000

New table only -- nothing existing is altered, so it is safe to apply with the
old code still running (the old code never touches it) and safe to roll back
(downgrade drops only this table).

UNIQUE (installation_id, fingerprint, app_version) is what makes intake an
upsert: one row per bug per build per install, with a running count. The two
indexes serve the staff page (group by fingerprint, newest first) and the
90-day retention prune (last_seen_at).

7c41d2e9a0b5 was claimed for this wave in ROADMAP.md before work started
(single-writer resource: exactly one new head on top of it).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = 'a3d9e6c14b72'
down_revision = '7c41d2e9a0b5'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('owner_crash_reports',
    sa.Column('installation_id', sa.UUID(), nullable=False),
    sa.Column('license_id', sa.UUID(), nullable=True),
    sa.Column('product_code', sa.String(length=64), nullable=False),
    sa.Column('platform', sa.String(length=32), nullable=False),
    sa.Column('app_version', sa.String(length=64), nullable=False),
    sa.Column('fingerprint', sa.String(length=32), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('exception_type', sa.String(length=200), nullable=False),
    sa.Column('message', sa.Text(), nullable=False),
    sa.Column('frames', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('route', sa.String(length=200), nullable=True),
    sa.Column('first_seen_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('count', sa.Integer(), nullable=False),
    sa.Column('received_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.ForeignKeyConstraint(['installation_id'], ['owner_installations.id'], ),
    sa.ForeignKeyConstraint(['license_id'], ['owner_licenses.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('installation_id', 'fingerprint', 'app_version', name='uq_owner_crash_reports_install_fp_version')
    )
    op.create_index('ix_owner_crash_reports_fingerprint', 'owner_crash_reports', ['fingerprint'], unique=False)
    op.create_index('ix_owner_crash_reports_last_seen_at', 'owner_crash_reports', ['last_seen_at'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_owner_crash_reports_last_seen_at', table_name='owner_crash_reports')
    op.drop_index('ix_owner_crash_reports_fingerprint', table_name='owner_crash_reports')
    op.drop_table('owner_crash_reports')
