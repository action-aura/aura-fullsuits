"""step0_commission_ladder_and_refund_stops (the owner's ladder + refund rules, 2026-10-24)

Revision ID: 38a13f8b2f8e
Revises: 1dfa1f6108b2
Create Date: 2026-10-24 22:00:00.000000

The owner's commission ladder (per seller, per calendar month in Jordan
time: 1 sale = 20, 2 = 50, 3 = 100, 4 = 200, 5 = 350 JOD, +30 each after),
the 10 JOD renewal, the 30-day hold, and "a refunded licence keeps working
2 days, then stops". Nothing in the schema could hold any of it: a ledger
row had to name an allocation (a monthly payment or a renewal has none), a
row had no month, no place on a ladder and no hold, and nothing could store
"this licence stops on this date". Measured on 1025d626.

Additive, except two NOT NULL -> NULL relaxations guarded by a CHECK:
  owner_commission_ladder_sales            one row per sale counted on a ladder (a fact row, never deleted)
  owner_license_scheduled_stops            a refund's stored "stop this licence at" moment
  owner_commission_ledger_entries
    entry_kind       PAYMENT_RULE (default -- every existing row) / LADDER / RENEWAL
    ladder_sale_id, ladder_month, ladder_position, payable_after   (NULL for existing rows)
    source_commercial_invoice_id, source_payment_allocation_id     NOT NULL -> NULL, but
      ck_owner_commission_ledger_entries_payment_rule_sources keeps every
      PAYMENT_RULE row (all existing rows) exactly as strict as before.

Existing rows: entry_kind backfills to PAYMENT_RULE by its server default,
which is what every one of them is; nothing else changes. Safe with the old
code running (it never writes the new kinds, and every row it writes is a
PAYMENT_RULE row with both sources). The downgrade refuses to run while any
non-PAYMENT_RULE row exists -- dropping those columns would silently erase
commission the company owes -- otherwise it restores the NOT NULLs and drops
exactly what this added.

1dfa1f6108b2 was named as this revision's down_revision in ROADMAP.md before
the migration was written (single-writer resource), commit 5835a952.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = '38a13f8b2f8e'
down_revision = '1dfa1f6108b2'
branch_labels = None
depends_on = None

_LEDGER = "owner_commission_ledger_entries"


def upgrade() -> None:
    op.create_table(
        "owner_commission_ladder_sales",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("employee_profile_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("owner_employee_profiles.id"), nullable=False),
        sa.Column("sales_order_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("owner_sales_orders.id"), nullable=False),
        sa.Column("commercial_invoice_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("owner_commercial_invoices.id"), nullable=False),
        sa.Column("subscription_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("owner_subscriptions.id"), nullable=True),
        sa.Column("package_plan_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("owner_plans.id"), nullable=False),
        sa.Column("commission_rule_version_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("owner_commission_rule_versions.id"), nullable=False),
        sa.Column("qualifying_payment_record_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("owner_payment_records.id"), nullable=False),
        sa.Column("qualifying_allocation_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("owner_payment_allocations.id"), nullable=True),
        sa.Column("qualified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ladder_month", sa.Date(), nullable=False),
        sa.Column("hold_ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="COUNTED"),
        sa.Column("uncounted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("uncount_cause", sa.String(24), nullable=True),
        sa.Column("uncount_reason", sa.Text(), nullable=True),
        sa.Column("source_refund_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("owner_commercial_refunds.id"), nullable=True),
        sa.Column("refunded_after_hold_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("status IN ('COUNTED', 'UNCOUNTED')", name="ck_owner_commission_ladder_sales_status"),
        sa.CheckConstraint(
            "uncount_cause IS NULL OR uncount_cause IN ('REFUND_IN_HOLD', 'CORRECTION')",
            name="ck_owner_commission_ladder_sales_uncount_cause",
        ),
        sa.CheckConstraint(
            "(status = 'COUNTED' AND uncounted_at IS NULL AND uncount_cause IS NULL) OR "
            "(status = 'UNCOUNTED' AND uncounted_at IS NOT NULL AND uncount_cause IS NOT NULL)",
            name="ck_owner_commission_ladder_sales_uncounted_has_cause",
        ),
    )
    op.create_index("uq_commission_ladder_one_counted_sale_per_order", "owner_commission_ladder_sales",
                    ["sales_order_id"], unique=True, postgresql_where=sa.text("status = 'COUNTED'"))
    op.create_index("ix_commission_ladder_sales_seller_month", "owner_commission_ladder_sales",
                    ["employee_profile_id", "ladder_month"])
    op.create_index("ix_owner_commission_ladder_sales_sales_order_id", "owner_commission_ladder_sales",
                    ["sales_order_id"])

    op.add_column(_LEDGER, sa.Column("entry_kind", sa.String(16), nullable=False, server_default="PAYMENT_RULE"))
    op.add_column(_LEDGER, sa.Column("ladder_sale_id", postgresql.UUID(as_uuid=True),
                                     sa.ForeignKey("owner_commission_ladder_sales.id"), nullable=True))
    op.add_column(_LEDGER, sa.Column("ladder_month", sa.Date(), nullable=True))
    op.add_column(_LEDGER, sa.Column("ladder_position", sa.Integer(), nullable=True))
    op.add_column(_LEDGER, sa.Column("payable_after", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_owner_commission_ledger_entries_ladder_sale_id", _LEDGER, ["ladder_sale_id"])
    op.alter_column(_LEDGER, "source_commercial_invoice_id", existing_type=postgresql.UUID(as_uuid=True), nullable=True)
    op.alter_column(_LEDGER, "source_payment_allocation_id", existing_type=postgresql.UUID(as_uuid=True), nullable=True)
    op.create_check_constraint("ck_owner_commission_ledger_entries_kind", _LEDGER,
                               "entry_kind IN ('PAYMENT_RULE', 'LADDER', 'RENEWAL')")
    op.create_check_constraint(
        "ck_owner_commission_ledger_entries_payment_rule_sources", _LEDGER,
        "entry_kind <> 'PAYMENT_RULE' OR "
        "(source_commercial_invoice_id IS NOT NULL AND source_payment_allocation_id IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_owner_commission_ledger_entries_ladder_sources", _LEDGER,
        "entry_kind <> 'LADDER' OR (ladder_sale_id IS NOT NULL AND ladder_month IS NOT NULL)",
    )
    op.create_index("uq_commission_ledger_one_open_ladder_entry_per_sale", _LEDGER, ["ladder_sale_id"], unique=True,
                    postgresql_where=sa.text("entry_kind = 'LADDER' AND status = 'EARNED'"))
    op.create_index(
        "uq_commission_ledger_one_renewal_per_payment", _LEDGER, ["source_payment_record_id"], unique=True,
        postgresql_where=sa.text(
            "entry_kind = 'RENEWAL' AND reversal_of_ledger_entry_id IS NULL AND status <> 'CANCELLED'"
        ),
    )

    op.create_table(
        "owner_license_scheduled_stops",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("license_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("owner_licenses.id"), nullable=False),
        sa.Column("subscription_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("owner_subscriptions.id"), nullable=True),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("to_status", sa.String(32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("source_refund_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("owner_commercial_refunds.id"), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_staff_user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("owner_staff_users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("source_refund_id", "license_id", name="uq_owner_license_scheduled_stops_refund_license"),
        sa.CheckConstraint("to_status IN ('SUSPENDED')", name="ck_owner_license_scheduled_stops_to_status"),
    )
    op.create_index("ix_owner_license_scheduled_stops_license_id", "owner_license_scheduled_stops", ["license_id"])
    op.create_index("ix_owner_license_scheduled_stops_due", "owner_license_scheduled_stops", ["effective_at"],
                    postgresql_where=sa.text("applied_at IS NULL AND cancelled_at IS NULL"))


def downgrade() -> None:
    bind = op.get_bind()
    owed = bind.execute(sa.text(f"SELECT count(*) FROM {_LEDGER} WHERE entry_kind <> 'PAYMENT_RULE'")).scalar_one()
    if owed:
        raise RuntimeError(
            f"Refusing to downgrade 38a13f8b2f8e: {owed} ladder/renewal commission row(s) exist and dropping "
            "their columns would erase commission the company owes. Export them first."
        )
    op.drop_index("ix_owner_license_scheduled_stops_due", table_name="owner_license_scheduled_stops")
    op.drop_index("ix_owner_license_scheduled_stops_license_id", table_name="owner_license_scheduled_stops")
    op.drop_table("owner_license_scheduled_stops")

    op.drop_index("uq_commission_ledger_one_renewal_per_payment", table_name=_LEDGER)
    op.drop_index("uq_commission_ledger_one_open_ladder_entry_per_sale", table_name=_LEDGER)
    op.drop_constraint("ck_owner_commission_ledger_entries_ladder_sources", _LEDGER, type_="check")
    op.drop_constraint("ck_owner_commission_ledger_entries_payment_rule_sources", _LEDGER, type_="check")
    op.drop_constraint("ck_owner_commission_ledger_entries_kind", _LEDGER, type_="check")
    op.alter_column(_LEDGER, "source_payment_allocation_id", existing_type=postgresql.UUID(as_uuid=True), nullable=False)
    op.alter_column(_LEDGER, "source_commercial_invoice_id", existing_type=postgresql.UUID(as_uuid=True), nullable=False)
    op.drop_index("ix_owner_commission_ledger_entries_ladder_sale_id", table_name=_LEDGER)
    op.drop_column(_LEDGER, "payable_after")
    op.drop_column(_LEDGER, "ladder_position")
    op.drop_column(_LEDGER, "ladder_month")
    op.drop_column(_LEDGER, "ladder_sale_id")
    op.drop_column(_LEDGER, "entry_kind")

    op.drop_index("ix_owner_commission_ladder_sales_sales_order_id", table_name="owner_commission_ladder_sales")
    op.drop_index("ix_commission_ladder_sales_seller_month", table_name="owner_commission_ladder_sales")
    op.drop_index("uq_commission_ladder_one_counted_sale_per_order", table_name="owner_commission_ladder_sales")
    op.drop_table("owner_commission_ladder_sales")
