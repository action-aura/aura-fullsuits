"""step0_refund_threshold_and_late_override (Step 0, 2026-10-25)

Revision ID: f28dfb3efc40
Revises: f16f0245da18
Create Date: 2026-10-25 14:00:00.000000

Two owner decisions of 2026-10-25 (commercial_sales/refund_policy.py):

1. A refund stops the licence only when the package money given back is
   ABOVE a percentage of the package money paid. The system default (50%)
   lives in the existing owner_system_settings table (no column); the owner
   may set a per-customer override:
     owner_customers.refund_licence_stop_threshold_percent   integer, NULL
         NULL = use the default; CHECK 0..100.
   And each refund keeps the decision it was paid out under, so a later
   change of the threshold never re-judges it:
     owner_commercial_refunds.threshold_percent_applied      integer, NULL
     owner_commercial_refunds.package_refund_over_threshold  boolean, NULL
         both or neither (CHECK), percent 0..100. Existing rows: neither
         (paid out before this rule; the code keeps the old rule for them).

2. A Super Admin may allow a refund after the 30 days with a written reason:
     owner_commercial_refunds.window_override_reason            text, NULL
     owner_commercial_refunds.window_override_by_staff_user_id  uuid FK staff, NULL
     owner_commercial_refunds.window_override_at                timestamptz, NULL
         all three or none, the reason at least 10 characters (CHECK).
   Such a refund takes the sale off the commission ladder with its own cause:
     owner_commission_ladder_sales.uncount_cause may also be REFUND_LATE_OVERRIDE.

Every CHECK branch is spelled with IS [NOT] NULL: a CHECK that evaluates to
NULL passes (f16f0245da18 measured its first spelling accepting a bad row).

Safe with the old code running: it never writes the new columns, and every
existing row satisfies every new CHECK (all NULL). The downgrade refuses
while any row uses what it would drop -- a customer's threshold, a refund's
decision or override, a ladder sale uncounted by a late override -- because
the old schema cannot hold them and the old code would re-judge those refunds
by the old rule.

f16f0245da18 was named as this revision's down_revision in ROADMAP.md before
the migration was written (single-writer resource), commit 2aa81ee8.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = 'f28dfb3efc40'
down_revision = 'f16f0245da18'
branch_labels = None
depends_on = None

_CUSTOMERS = "owner_customers"
_REFUNDS = "owner_commercial_refunds"
_LADDER = "owner_commission_ladder_sales"
_CAUSE_CHECK = "ck_owner_commission_ladder_sales_uncount_cause"


def upgrade() -> None:
    op.add_column(_CUSTOMERS, sa.Column("refund_licence_stop_threshold_percent", sa.Integer(), nullable=True))
    op.create_check_constraint(
        "ck_owner_customers_refund_threshold_percent", _CUSTOMERS,
        "refund_licence_stop_threshold_percent IS NULL OR "
        "(refund_licence_stop_threshold_percent >= 0 AND refund_licence_stop_threshold_percent <= 100)",
    )

    op.add_column(_REFUNDS, sa.Column("threshold_percent_applied", sa.Integer(), nullable=True))
    op.add_column(_REFUNDS, sa.Column("package_refund_over_threshold", sa.Boolean(), nullable=True))
    op.add_column(_REFUNDS, sa.Column("window_override_reason", sa.Text(), nullable=True))
    op.add_column(_REFUNDS, sa.Column("window_override_by_staff_user_id", postgresql.UUID(as_uuid=True),
                                      sa.ForeignKey("owner_staff_users.id"), nullable=True))
    op.add_column(_REFUNDS, sa.Column("window_override_at", sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint(
        "ck_owner_commercial_refunds_threshold_decision", _REFUNDS,
        "(threshold_percent_applied IS NULL AND package_refund_over_threshold IS NULL) OR "
        "(threshold_percent_applied IS NOT NULL AND package_refund_over_threshold IS NOT NULL "
        "AND threshold_percent_applied >= 0 AND threshold_percent_applied <= 100)",
    )
    op.create_check_constraint(
        "ck_owner_commercial_refunds_window_override", _REFUNDS,
        "(window_override_at IS NULL AND window_override_by_staff_user_id IS NULL "
        "AND window_override_reason IS NULL) OR "
        "(window_override_at IS NOT NULL AND window_override_by_staff_user_id IS NOT NULL "
        "AND window_override_reason IS NOT NULL AND length(btrim(window_override_reason)) >= 10)",
    )

    op.drop_constraint(_CAUSE_CHECK, _LADDER, type_="check")
    op.create_check_constraint(
        _CAUSE_CHECK, _LADDER,
        "uncount_cause IS NULL OR uncount_cause IN ('REFUND_IN_HOLD', 'CORRECTION', 'REFUND_LATE_OVERRIDE')",
    )


def downgrade() -> None:
    bind = op.get_bind()
    in_use = {
        "customer refund thresholds": f"SELECT count(*) FROM {_CUSTOMERS} "
                                      "WHERE refund_licence_stop_threshold_percent IS NOT NULL",
        "refund threshold decisions": f"SELECT count(*) FROM {_REFUNDS} WHERE threshold_percent_applied IS NOT NULL",
        "late-refund overrides": f"SELECT count(*) FROM {_REFUNDS} WHERE window_override_at IS NOT NULL",
        "ladder sales uncounted by a late refund": f"SELECT count(*) FROM {_LADDER} "
                                                   "WHERE uncount_cause = 'REFUND_LATE_OVERRIDE'",
    }
    found = {what: bind.execute(sa.text(sql)).scalar_one() for what, sql in in_use.items()}
    found = {what: count for what, count in found.items() if count}
    if found:
        listed = ", ".join(f"{count} {what}" for what, count in found.items())
        raise RuntimeError(
            f"Refusing to downgrade f28dfb3efc40: {listed} exist and the old schema cannot hold them "
            "(the old code would re-judge those refunds by the old rule). Export them first."
        )
    op.drop_constraint(_CAUSE_CHECK, _LADDER, type_="check")
    op.create_check_constraint(_CAUSE_CHECK, _LADDER,
                               "uncount_cause IS NULL OR uncount_cause IN ('REFUND_IN_HOLD', 'CORRECTION')")
    op.drop_constraint("ck_owner_commercial_refunds_window_override", _REFUNDS, type_="check")
    op.drop_constraint("ck_owner_commercial_refunds_threshold_decision", _REFUNDS, type_="check")
    for column in ("window_override_at", "window_override_by_staff_user_id", "window_override_reason",
                   "package_refund_over_threshold", "threshold_percent_applied"):
        op.drop_column(_REFUNDS, column)
    op.drop_constraint("ck_owner_customers_refund_threshold_percent", _CUSTOMERS, type_="check")
    op.drop_column(_CUSTOMERS, "refund_licence_stop_threshold_percent")
