"""phase_9_5d_missing_fk_indexes

Revision ID: b2f6a8e13c74
Revises: a3c8e5d29f47
Create Date: 2026-08-02 16:40:00.000000

Milestone 22 (DB/migrations/indexes/numbering validation) -- real gap
found, matching the exact class of bug Phase 9.5C's own Milestone 24
found ("a repo-wide missing-index gap across every CRM ownership/child-
lookup query"): every FK column actually used in a real .where(X == ...)
clause across app/commercial_sales/*.py, app/commissions/*.py, and their
routes was audited; 12 were missing an index despite being queried on
every list/detail page load (ownership filter via
apply_ownership_filter()) or every document-detail page (line/child
lookups). PaymentAllocation.commercial_invoice_id/payment_record_id and
CommissionLedgerEntry.source_payment_allocation_id were already correctly
indexed by their own originating milestones (M11/M15) -- not touched
here, listed for completeness of the audit trail.
"""
from alembic import op


revision = 'b2f6a8e13c74'
down_revision = 'a3c8e5d29f47'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index('ix_owner_quotes_created_by_employee_profile_id', 'owner_quotes', ['created_by_employee_profile_id'])
    op.create_index('ix_owner_quote_lines_quote_id', 'owner_quote_lines', ['quote_id'])
    op.create_index('ix_owner_sales_orders_quote_id', 'owner_sales_orders', ['quote_id'])
    op.create_index('ix_owner_sales_orders_created_by_employee_profile_id', 'owner_sales_orders', ['created_by_employee_profile_id'])
    op.create_index('ix_owner_sales_order_lines_sales_order_id', 'owner_sales_order_lines', ['sales_order_id'])
    op.create_index('ix_owner_commercial_invoices_sales_order_id', 'owner_commercial_invoices', ['sales_order_id'])
    op.create_index('ix_owner_commercial_invoices_created_by_employee_profile_id', 'owner_commercial_invoices', ['created_by_employee_profile_id'])
    op.create_index('ix_owner_commercial_invoice_items_commercial_invoice_id', 'owner_commercial_invoice_items', ['commercial_invoice_id'])
    op.create_index('ix_owner_commercial_approvals_requested_by_staff_user_id', 'owner_commercial_approvals', ['requested_by_staff_user_id'])
    op.create_index('ix_owner_commission_ledger_entries_employee_profile_id', 'owner_commission_ledger_entries', ['employee_profile_id'])
    op.create_index('ix_owner_commission_ledger_entries_source_commercial_invoice_id', 'owner_commission_ledger_entries', ['source_commercial_invoice_id'])
    op.create_index('ix_owner_commission_plan_assignment_employee_profile_id', 'owner_employee_commission_plan_assignments', ['employee_profile_id'])


def downgrade() -> None:
    op.drop_index('ix_owner_commission_plan_assignment_employee_profile_id', table_name='owner_employee_commission_plan_assignments')
    op.drop_index('ix_owner_commission_ledger_entries_source_commercial_invoice_id', table_name='owner_commission_ledger_entries')
    op.drop_index('ix_owner_commission_ledger_entries_employee_profile_id', table_name='owner_commission_ledger_entries')
    op.drop_index('ix_owner_commercial_approvals_requested_by_staff_user_id', table_name='owner_commercial_approvals')
    op.drop_index('ix_owner_commercial_invoice_items_commercial_invoice_id', table_name='owner_commercial_invoice_items')
    op.drop_index('ix_owner_commercial_invoices_created_by_employee_profile_id', table_name='owner_commercial_invoices')
    op.drop_index('ix_owner_commercial_invoices_sales_order_id', table_name='owner_commercial_invoices')
    op.drop_index('ix_owner_sales_order_lines_sales_order_id', table_name='owner_sales_order_lines')
    op.drop_index('ix_owner_sales_orders_created_by_employee_profile_id', table_name='owner_sales_orders')
    op.drop_index('ix_owner_sales_orders_quote_id', table_name='owner_sales_orders')
    op.drop_index('ix_owner_quote_lines_quote_id', table_name='owner_quote_lines')
    op.drop_index('ix_owner_quotes_created_by_employee_profile_id', table_name='owner_quotes')
