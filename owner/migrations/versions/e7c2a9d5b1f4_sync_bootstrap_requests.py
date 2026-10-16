"""sync bootstrap mailbox requests (SYNC-BOOT Phase 3, revision B)

Revision ID: e7c2a9d5b1f4
Revises: d3b8f1a7c4e2
Create Date: 2026-10-13 18:00:00.000000

Why (SYNC-BOOT plan sections 2.1, 2.2, 5.1; ROADMAP claim of 2026-10-13):
Phase 1 made Owner answer a pull below the pruned range with 409
RESYNC_REQUIRED. Something has to recover that device. A peer holding the shop's
data builds an encrypted copy and Owner relays it; this table is the mailbox's
ledger (who asked, who claimed, what state). The encrypted bytes are NOT here:
they live on a filesystem directory (OWNER_SYNC_BOOTSTRAP_DIR) so Neon's free
quota is never spent on them and they never enter a database backup.

The partial unique index is the "one open request per (installation, stream)"
rule enforced by the database, so a device retrying every ten seconds, or two
requests racing, cannot create a pile.

Downgrade drops the table: it holds only transient requests (72 h TTL), so
nothing durable is lost, but any copy in flight is abandoned.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = 'e7c2a9d5b1f4'
down_revision = 'd3b8f1a7c4e2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "owner_sync_bootstrap_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("license_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("owner_licenses.id"), nullable=False),
        sa.Column("requester_installation_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("owner_installations.id"), nullable=False),
        sa.Column("stream", sa.String(16), nullable=False),
        sa.Column("reason", sa.String(32), nullable=False, server_default="join"),
        sa.Column("status", sa.String(16), nullable=False, server_default="OPEN"),
        sa.Column("min_as_of", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("fast_forward_to", sa.BigInteger(), nullable=True),
        sa.Column("requester_schema_version", sa.Integer(), nullable=True),
        sa.Column("requester_app_version", sa.String(64), nullable=True),
        sa.Column("enc_pubkey", sa.String(64), nullable=False),
        sa.Column("producer_installation_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("owner_installations.id"), nullable=True),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("manifest", postgresql.JSONB(), nullable=True),
        sa.Column("blob_ref", sa.String(128), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "uq_owner_sync_bootstrap_open_per_requester_stream", "owner_sync_bootstrap_requests",
        ["requester_installation_id", "stream"], unique=True,
        postgresql_where=sa.text("status IN ('OPEN','CLAIMED','READY','DELIVERED')"),
    )
    op.create_index("ix_owner_sync_bootstrap_license_status", "owner_sync_bootstrap_requests",
                    ["license_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_owner_sync_bootstrap_license_status", table_name="owner_sync_bootstrap_requests")
    op.drop_index("uq_owner_sync_bootstrap_open_per_requester_stream", table_name="owner_sync_bootstrap_requests")
    op.drop_table("owner_sync_bootstrap_requests")
