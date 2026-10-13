"""sync per-stream cursors + pruned_through (SYNC-BOOT Phase 1, revision A)

Revision ID: d3b8f1a7c4e2
Revises: c7a4f2d8e613
Create Date: 2026-10-13 15:00:00.000000

Why (docs: SYNC-BOOT plan sections 0, 4, 5.1; ROADMAP.md claim of 2026-10-13):

1. owner_sync_device_cursors was keyed by installation_id alone, but one
   installation runs TWO sync streams (retail and registry) that share that id
   and each track their own cursor. One row therefore followed whichever stream
   pulled furthest, and pruning (DELETE seq <= MIN over devices) could delete
   events the lagging stream had never applied. The PK becomes
   (installation_id, stream). Every pre-existing row becomes stream='legacy'
   (what an old client, which sends no stream, still uses).
   last_pulled_at (liveness) and caps (declared capabilities) are added for the
   later phases; nothing in Phase 1 depends on them.

2. owner_sync_license_state.pruned_through_seq records how far a licence's log
   has been pruned, so pull() can answer RESYNC_REQUIRED instead of returning a
   partial stream that looks gap-free.

BACKFILL (the only judgement call in this revision). Production was already
being pruned before pruned_through existed, so the number is unknown. We
reconstruct a SAFE LOWER-BOUND-ON-DAMAGE estimate: for each licence that has a
cursor at or beyond its oldest retained event, pruned_through = oldest retained
seq - 1. Meaning: "nothing older than what is on disk is claimed to be
deliverable". This errs toward a loud RESYNC_REQUIRED, never toward silently
serving a partial history. KNOWN COST of erring that way: seq is one identity
sequence shared by ALL licences, so a licence whose very first event happens to
have seq > 1 gets pruned_through = first_seq - 1 even though nothing was ever
deleted; a brand-new device pulling since=0 would be told to resync. Phase 3
(bootstrap) is what lets such a device recover; until then the operator can set
the row to 0 by hand if the licence provably never pruned.
A licence with no cursor at or beyond its oldest event (nothing proves a prune
ran, or no device ever advanced) gets NO row (= 0).

Downgrade collapses (installation_id, stream) back to installation_id, keeping
the MOST ADVANCED row per installation; that can only make a device look
further ahead than the slowest stream, i.e. it is lossy and is for development
rollback only -- do not downgrade a live database.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = 'd3b8f1a7c4e2'
down_revision = 'c7a4f2d8e613'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "owner_sync_device_cursors",
        sa.Column("stream", sa.String(16), nullable=False, server_default="legacy"),
    )
    op.add_column("owner_sync_device_cursors", sa.Column("last_pulled_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("owner_sync_device_cursors", sa.Column("caps", postgresql.JSONB(), nullable=True))
    op.drop_constraint("owner_sync_device_cursors_pkey", "owner_sync_device_cursors", type_="primary")
    op.create_primary_key("owner_sync_device_cursors_pkey", "owner_sync_device_cursors", ["installation_id", "stream"])

    op.create_table(
        "owner_sync_license_state",
        sa.Column("license_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("owner_licenses.id"), primary_key=True),
        sa.Column("pruned_through_seq", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("last_prune_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # Backfill: see the module docstring. Only licences that have BOTH events
    # and a cursor at/beyond the oldest retained event get a row.
    op.execute(
        """
        INSERT INTO owner_sync_license_state (license_id, pruned_through_seq)
        SELECT e.license_id, MIN(e.seq) - 1
        FROM owner_sync_events e
        GROUP BY e.license_id
        HAVING MIN(e.seq) - 1 > 0
           AND EXISTS (
               SELECT 1 FROM owner_sync_device_cursors c
               WHERE c.license_id = e.license_id AND c.last_acked_seq >= MIN(e.seq)
           )
        """
    )


def downgrade() -> None:
    op.drop_table("owner_sync_license_state")
    op.execute(
        """
        DELETE FROM owner_sync_device_cursors a
        USING owner_sync_device_cursors b
        WHERE a.installation_id = b.installation_id
          AND (a.last_acked_seq < b.last_acked_seq
               OR (a.last_acked_seq = b.last_acked_seq AND a.stream > b.stream))
        """
    )
    op.drop_constraint("owner_sync_device_cursors_pkey", "owner_sync_device_cursors", type_="primary")
    op.create_primary_key("owner_sync_device_cursors_pkey", "owner_sync_device_cursors", ["installation_id"])
    op.drop_column("owner_sync_device_cursors", "caps")
    op.drop_column("owner_sync_device_cursors", "last_pulled_at")
    op.drop_column("owner_sync_device_cursors", "stream")
