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

BACKFILL: NONE (deliberately). An earlier draft inferred pruned_through from
"oldest retained seq - 1" for licences with an advanced cursor. That was wrong:
seq is ONE identity sequence shared by ALL licences, so a licence that was
never pruned, whose first event simply sits at seq 5000, would be claimed
pruned through 4999 and every NEW device (since=0) would get 409
RESYNC_REQUIRED with no bootstrap until Phase 3 -- joining a shop broken for
nearly every licence. No row exists after this migration (= 0 = "nothing
pruned"). pruned_through is raised ONLY by a real `sync prune-events --apply`
deletion (same transaction as the delete, per-licence highest deleted seq).
A live relay that already pruned before this column existed is corrected by
the operator with `flask sync mark-pruned --license <id> [--through N]`
(use --dry-run first); see ROADMAP "SYNC-BOOT Phase 1" amendment.

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
