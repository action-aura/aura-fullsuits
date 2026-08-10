"""phase_9_5e_management_notes_missing_indexes

Revision ID: f5959fdb9738
Revises: 3f95d792998c
Create Date: 2026-08-03 09:03:53.411330

"""
from alembic import op
import sqlalchemy as sa


revision = 'f5959fdb9738'
down_revision = '3f95d792998c'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Real gap found by Phase 9.5E Milestone 23's EXPLAIN ANALYZE at scale
    # (20k-row synthetic dataset): the 9.5A management_notes tables had
    # zero indexes beyond their primary keys -- the same class of bug
    # Phase 9.5C's own M24 found for CRM ownership queries. This phase
    # built the first real service layer/queries against these tables
    # (notes_visible_to(), can_view_note()), so this is the first time the
    # gap was ever exercised, not a regression.
    op.create_index("ix_owner_shared_management_notes_status", "owner_shared_management_notes", ["status"])
    op.create_index("ix_owner_shared_management_notes_visibility", "owner_shared_management_notes", ["visibility"])
    op.create_index("ix_owner_shared_management_notes_assigned_employee_profile_id", "owner_shared_management_notes", ["assigned_employee_profile_id"])
    op.create_index("ix_owner_shared_management_notes_created_by_staff_user_id", "owner_shared_management_notes", ["created_by_staff_user_id"])
    op.create_index("ix_owner_management_note_visibility_grants_management_note_id", "owner_management_note_visibility_grants", ["management_note_id"])
    op.create_index("ix_owner_management_note_visibility_grants_employee_profile_id", "owner_management_note_visibility_grants", ["employee_profile_id"])
    op.create_index("ix_owner_management_note_comments_management_note_id", "owner_management_note_comments", ["management_note_id"])


def downgrade() -> None:
    op.drop_index("ix_owner_management_note_comments_management_note_id", table_name="owner_management_note_comments")
    op.drop_index("ix_owner_management_note_visibility_grants_employee_profile_id", table_name="owner_management_note_visibility_grants")
    op.drop_index("ix_owner_management_note_visibility_grants_management_note_id", table_name="owner_management_note_visibility_grants")
    op.drop_index("ix_owner_shared_management_notes_created_by_staff_user_id", table_name="owner_shared_management_notes")
    op.drop_index("ix_owner_shared_management_notes_assigned_employee_profile_id", table_name="owner_shared_management_notes")
    op.drop_index("ix_owner_shared_management_notes_visibility", table_name="owner_shared_management_notes")
    op.drop_index("ix_owner_shared_management_notes_status", table_name="owner_shared_management_notes")
