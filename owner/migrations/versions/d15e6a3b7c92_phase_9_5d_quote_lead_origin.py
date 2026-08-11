"""phase_9_5d_quote_lead_origin

Revision ID: d15e6a3b7c92
Revises: c92d5f18a4e6
Create Date: 2026-08-02 10:45:00.000000

Non-Negotiable Rule 13 requires "a Quote may originate from a qualified
Lead," but the original Phase 9.5A migration only ever gave Quote a
required customer_id -- no way to represent a Lead-based Quote at all.
Relaxes customer_id to nullable, adds lead_id, and adds a CHECK
constraint requiring at least one (never neither) -- NOT exactly-one,
since once a Lead-based Quote's boundary conversion runs (Milestone 7),
customer_id is populated while lead_id is deliberately retained as the
permanent historical origin marker (same pattern as
Customer.converted_from_lead_id). SalesOrder.customer_id stays NOT NULL
unchanged (Non-Negotiable Rule 13: "A Sales Order and all downstream
documents require a confirmed Customer" -- conversion must already have
happened by Order creation time).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = 'd15e6a3b7c92'
down_revision = 'c92d5f18a4e6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column('owner_quotes', 'customer_id', nullable=True)
    op.add_column('owner_quotes', sa.Column('lead_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('owner_leads.id'), nullable=True))
    op.create_check_constraint(
        'ck_owner_quotes_at_least_one_of_customer_lead',
        'owner_quotes',
        '(customer_id IS NOT NULL)::int + (lead_id IS NOT NULL)::int >= 1',
    )


def downgrade() -> None:
    op.drop_constraint('ck_owner_quotes_at_least_one_of_customer_lead', 'owner_quotes', type_='check')
    op.drop_column('owner_quotes', 'lead_id')
    op.alter_column('owner_quotes', 'customer_id', nullable=False)
