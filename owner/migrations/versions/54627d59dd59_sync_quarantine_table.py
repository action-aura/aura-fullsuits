"""sync_quarantine table

Revision ID: 54627d59dd59
Revises: 15bfb9e618a3
Create Date: 2026-08-24 09:05:00.000000

Phase 5 prerequisite #3 (docs/launch-readiness/phase5-prerequisites.md
section 3, ROADMAP.md's "Two things Phase 5 must not open the firehose
without") -- one row per push-batch event that failed validation (or was
rejected by Postgres itself, e.g. a NUL byte) and was therefore SKIPPED
rather than rolling back the entire batch. Before this table existed,
apply was strictly all-or-nothing (routes.py rolled back the whole batch
and returned 400 INVALID_EVENT on any single bad row) -- one malformed
event from one client would stop that shop syncing forever, since every
retry hits the identical batch and fails on the identical row.

`raw_payload` stores the event exactly as sent (verbatim, JSONB, no
partial parsing/normalization) so a fixed submission (see
app/sync/quarantine_routes.py::replay) reproduces precisely what the
device originally pushed and an operator can see exactly what was sent.
`status` starts PENDING and moves to REPLAYED or DISCARDED -- rows are
never DELETEd; every quarantined event stays visible in the Owner
console regardless of resolution ("nothing is ever silently dropped").
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = '54627d59dd59'
down_revision = '15bfb9e618a3'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "owner_sync_quarantine",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("license_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("owner_licenses.id"), nullable=False),
        sa.Column("device_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("batch_index", sa.Integer(), nullable=False),
        sa.Column("raw_payload", postgresql.JSONB(), nullable=False),
        sa.Column("rejection_reason", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="PENDING"),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by_staff_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("owner_staff_users.id"), nullable=True),
    )
    op.create_index(
        "ix_owner_sync_quarantine_license_status", "owner_sync_quarantine", ["license_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_owner_sync_quarantine_license_status", table_name="owner_sync_quarantine")
    op.drop_table("owner_sync_quarantine")
