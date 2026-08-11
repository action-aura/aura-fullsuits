"""phase_9_5c_crm_ownership_and_child_lookup_indexes

Revision ID: a1f9c3d76e02
Revises: dd408948bf89
Create Date: 2026-08-02 00:15:00.000000

Milestone 24 real-scale EXPLAIN ANALYZE (100k synthetic owner_leads rows,
~3.3% per-employee selectivity) found that the CRM ownership-filter and
child-collection lookup columns had NO index beyond each table's primary
key -- including owner_leads.assigned_employee_profile_id /
.created_by_employee_profile_id, the single most frequently executed
query in this whole phase (apply_ownership_filter(), run on every Lead
list/dashboard/count call). The crm-index-and-query-plan.md doc's
Milestone 20 claim that these were "pre-existing from Phase 9.5A" was
verified false against the real dev database (pg_indexes) and corrected
alongside this migration.

Every column indexed here was confirmed, by grep against the real
query code (app/leads/, app/customers/, app/api_operations/crm.py), to
be an actual WHERE-clause equality predicate -- not speculative.
"""
from alembic import op


revision = 'a1f9c3d76e02'
down_revision = 'dd408948bf89'
branch_labels = None
depends_on = None


_INDEXES = [
    ("ix_owner_leads_assigned_employee_profile_id", "owner_leads", "assigned_employee_profile_id"),
    ("ix_owner_leads_created_by_employee_profile_id", "owner_leads", "created_by_employee_profile_id"),
    ("ix_owner_customers_assigned_sales_staff_id", "owner_customers", "assigned_sales_staff_id"),
    ("ix_owner_lead_assignments_lead_id", "owner_lead_assignments", "lead_id"),
    ("ix_owner_lead_status_history_lead_id", "owner_lead_status_history", "lead_id"),
    ("ix_owner_lead_interactions_lead_id", "owner_lead_interactions", "lead_id"),
    ("ix_owner_lead_interactions_employee_profile_id", "owner_lead_interactions", "employee_profile_id"),
    ("ix_owner_lead_followups_lead_id", "owner_lead_followups", "lead_id"),
    ("ix_owner_lead_followups_employee_profile_id", "owner_lead_followups", "employee_profile_id"),
    ("ix_owner_lead_notes_lead_id", "owner_lead_notes", "lead_id"),
    ("ix_owner_customer_locations_lead_id", "owner_customer_locations", "lead_id"),
    ("ix_owner_customer_locations_customer_id", "owner_customer_locations", "customer_id"),
    ("ix_owner_customer_contacts_customer_id", "owner_customer_contacts", "customer_id"),
    ("ix_owner_customer_interactions_customer_id", "owner_customer_interactions", "customer_id"),
    ("ix_owner_customer_followups_customer_id", "owner_customer_followups", "customer_id"),
    ("ix_owner_customer_notes_customer_id", "owner_customer_notes", "customer_id"),
]


def upgrade() -> None:
    for index_name, table_name, column_name in _INDEXES:
        op.create_index(index_name, table_name, [column_name])


def downgrade() -> None:
    for index_name, table_name, _ in reversed(_INDEXES):
        op.drop_index(index_name, table_name=table_name)
