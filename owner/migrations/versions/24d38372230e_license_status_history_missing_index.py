"""license_status_history_missing_index

Revision ID: 24d38372230e
Revises: c52eef9a82cc
Create Date: 2026-08-23 17:15:56.757877

AUDIT-perf follow-up: LicenseStatusHistory.license_id
(app/models/licensing.py:68) is a non-nullable FK looked up per suspended
license -- app/attention/service.py::_suspended_license_items() batches it
into one `WHERE license_id IN (...)` query per Attention Center render (see
b2f6a8e13c74_phase_9_5d_missing_fk_indexes.py, the sibling migration this
one deliberately matches the shape of) -- but the column itself has never
had an index, unlike PaymentAllocation's FKs
(commercial_invoice_id/payment_record_id, both index=True since their
originating milestone). Index only: no column, constraint, or data change.
"""
from alembic import op
import sqlalchemy as sa


revision = '24d38372230e'
down_revision = 'c52eef9a82cc'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        'ix_owner_license_status_history_license_id',
        'owner_license_status_history',
        ['license_id'],
    )


def downgrade() -> None:
    op.drop_index(
        'ix_owner_license_status_history_license_id',
        table_name='owner_license_status_history',
    )
