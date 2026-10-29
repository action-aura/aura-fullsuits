"""keep_selling_release_kind (keep-selling, 2026-10-30)

Revision ID: d4b8f1a62e07
Revises: a29865d5eab4
Create Date: 2026-10-30 12:00:00.000000

Owner decision 2026-10-24: a shop whose subscription lapsed UNPAID keeps
selling and still receives SAFETY fixes, but not FEATURE updates. The update
gate (app/releases/discovery.py, distribution.py) needs a field that says which
a release is; ProductVersion had none (release_channel is the STABLE/RC wave,
forced_upgrade_threshold means "mandatory").

One column on owner_product_versions:

  release_kind  VARCHAR(16) NOT NULL DEFAULT 'FEATURE'
                CHECK (release_kind IN ('FEATURE', 'SAFETY'))

Every existing row becomes FEATURE through the server default, so nothing is
newly offered to an unpaid shop by this migration alone: staff must mark a
release SAFETY (upload form / release manifest) before it reaches one.

Safe with the old code running: it never reads or writes the column and the
server default fills it on insert. The downgrade drops the column and its
CHECK; what is lost is the FEATURE/SAFETY label only (after a downgrade every
release reads as an ordinary one again), never a release, a file or a licence.

d4b8f1a62e07 was claimed in ROADMAP.md before this migration was written
(single-writer resource), commit e6757e17.
"""
from alembic import op
import sqlalchemy as sa


revision = 'd4b8f1a62e07'
down_revision = 'a29865d5eab4'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'owner_product_versions',
        sa.Column('release_kind', sa.String(length=16), server_default='FEATURE', nullable=False),
    )
    op.create_check_constraint(
        'ck_owner_product_versions_release_kind', 'owner_product_versions',
        "release_kind IN ('FEATURE', 'SAFETY')",
    )


def downgrade() -> None:
    op.drop_constraint('ck_owner_product_versions_release_kind', 'owner_product_versions', type_='check')
    op.drop_column('owner_product_versions', 'release_kind')
