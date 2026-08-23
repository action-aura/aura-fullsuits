"""license_entitlement_and_issuance_event_missing_indexes

Revision ID: d8dfeb46d1d6
Revises: 24d38372230e
Create Date: 2026-08-23 20:19:33.384910

AUDIT-perf follow-up #2: LicenseEntitlement.license_id
(app/models/licensing.py:89) and LicenseKeyIssuanceEvent.license_id
(app/models/licensing.py:102) are the same unindexed-FK shape as
LicenseStatusHistory.license_id, indexed by
24d38372230e_license_status_history_missing_index.py -- both are
non-nullable FKs on `License` (owner_licenses.id) via the
`License.entitlements` / `License.issuance_events` relationships and had
never had an index, unlike PaymentAllocation's FKs
(commercial_invoice_id/payment_record_id, both index=True since their
originating milestone). Index only: no column, constraint, or data
change.
"""
from alembic import op
import sqlalchemy as sa


revision = 'd8dfeb46d1d6'
down_revision = '24d38372230e'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        'ix_owner_license_entitlements_license_id',
        'owner_license_entitlements',
        ['license_id'],
    )
    op.create_index(
        'ix_owner_license_key_issuance_events_license_id',
        'owner_license_key_issuance_events',
        ['license_id'],
    )


def downgrade() -> None:
    op.drop_index(
        'ix_owner_license_key_issuance_events_license_id',
        table_name='owner_license_key_issuance_events',
    )
    op.drop_index(
        'ix_owner_license_entitlements_license_id',
        table_name='owner_license_entitlements',
    )
