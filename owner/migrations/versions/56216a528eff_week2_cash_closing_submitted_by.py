"""week2_cash_closing_submitted_by

Revision ID: 56216a528eff
Revises: 86e9229f85c1
Create Date: 2026-08-10 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = '56216a528eff'
down_revision = '86e9229f85c1'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # AUDIT-032 -- Additive only. submitted_by_staff_user_id records who
    # actually supplied the counted-cash figure via submit_closing(),
    # distinct from prepared_by_staff_user_id (set once at DRAFT creation
    # and never reassigned -- see app/models/cash_closing.py's class
    # docstring). decide_closing()'s self-approval block previously only
    # compared the approver against prepared_by, so a second preparer who
    # submitted someone else's draft could approve their own submission.
    # Nullable so every pre-existing closing (submitted before this
    # column existed) is unaffected.
    op.add_column(
        "owner_cash_closings",
        sa.Column("submitted_by_staff_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("owner_staff_users.id")),
    )


def downgrade() -> None:
    op.drop_column("owner_cash_closings", "submitted_by_staff_user_id")
