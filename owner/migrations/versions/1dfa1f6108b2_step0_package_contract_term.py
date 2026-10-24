"""step0_package_contract_term (the owner's three packages, 2026-10-24)

Revision ID: 1dfa1f6108b2
Revises: e7c2a9d5b1f4
Create Date: 2026-10-24 18:00:00.000000

The owner's Package 2 (35 JOD a month) and Package 3 (20 JOD a month) are
sold on a 12- and a 24-month contract. Nothing in the schema could say so:
a Plan had a billing interval (how often money is due) but no contract
length (how many months the customer has committed to), and a Subscription
had only its term dates, which renewals move. Measured on 7571c3df.

Additive only, three nullable columns, no backfill:
  owner_plans.contract_term_months          the package's contract, NULL = none (Package 1)
  owner_subscriptions.contract_term_months  copied from the plan when the sale is fulfilled
  owner_subscriptions.contract_ends_on      start + term, fixed at the sale

contract_ends_on is stored, not derived from start_date: start_date/end_date
are the LICENCE term (the paid period), and every renewal moves them. The
contract is a fact of the sale and must not move with them. Existing rows
keep NULL = no contract, which is exactly what they were sold as.

Safe with the old code still running (it never reads these columns) and
safe to roll back (downgrade drops only these columns and their checks).

e7c2a9d5b1f4 was named as this revision's down_revision in ROADMAP.md before
the migration was written (single-writer resource: one new head on top of it).
"""
from alembic import op
import sqlalchemy as sa


revision = '1dfa1f6108b2'
down_revision = 'e7c2a9d5b1f4'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("owner_plans", sa.Column("contract_term_months", sa.Integer(), nullable=True))
    op.create_check_constraint(
        "ck_owner_plans_contract_term_positive", "owner_plans",
        "contract_term_months IS NULL OR contract_term_months > 0",
    )
    op.add_column("owner_subscriptions", sa.Column("contract_term_months", sa.Integer(), nullable=True))
    op.add_column("owner_subscriptions", sa.Column("contract_ends_on", sa.Date(), nullable=True))
    op.create_check_constraint(
        "ck_owner_subscriptions_contract_term_positive", "owner_subscriptions",
        "contract_term_months IS NULL OR contract_term_months > 0",
    )


def downgrade() -> None:
    op.drop_constraint("ck_owner_subscriptions_contract_term_positive", "owner_subscriptions", type_="check")
    op.drop_column("owner_subscriptions", "contract_ends_on")
    op.drop_column("owner_subscriptions", "contract_term_months")
    op.drop_constraint("ck_owner_plans_contract_term_positive", "owner_plans", type_="check")
    op.drop_column("owner_plans", "contract_term_months")
