"""phase_9_5e_expense_payee_approval_payment_attachment_cashclosing_reportsnapshot

Revision ID: e8fb57c82a58
Revises: b2f6a8e13c74
Create Date: 2026-08-02 20:48:18.495704

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = 'e8fb57c82a58'
down_revision = 'b2f6a8e13c74'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "owner_expense_payees",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("payee_type", sa.String(16), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("employee_profile_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("owner_employee_profiles.id")),
        sa.Column("external_contact_reference", sa.String(255)),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by_staff_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("owner_staff_users.id"), nullable=False),
    )
    op.create_index("ix_owner_expense_payees_employee_profile_id", "owner_expense_payees", ["employee_profile_id"])

    op.add_column("owner_expenses", sa.Column("expense_number", sa.String(32)))
    op.add_column("owner_expenses", sa.Column("payee_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("owner_expense_payees.id")))
    op.add_column("owner_expenses", sa.Column("beneficiary_employee_profile_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("owner_employee_profiles.id")))
    op.add_column("owner_expenses", sa.Column("approved_amount", sa.Numeric(12, 2)))
    op.add_column("owner_expenses", sa.Column("external_reference", sa.String(200)))
    op.create_unique_constraint("uq_expense_number", "owner_expenses", ["expense_number"])
    op.create_index("ix_owner_expenses_payee_id", "owner_expenses", ["payee_id"])
    op.create_index("ix_owner_expenses_beneficiary_employee_profile_id", "owner_expenses", ["beneficiary_employee_profile_id"])
    op.create_index("ix_owner_expenses_entered_by_employee_profile_id", "owner_expenses", ["entered_by_employee_profile_id"])
    op.create_index("ix_owner_expenses_category_id", "owner_expenses", ["category_id"])
    op.create_index("ix_owner_expenses_external_reference", "owner_expenses", ["external_reference"])
    op.create_index("ix_owner_expenses_status", "owner_expenses", ["status"])

    op.create_table(
        "owner_expense_approvals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expense_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("owner_expenses.id"), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="PENDING"),
        sa.Column("requested_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("approved_amount", sa.Numeric(12, 2)),
        sa.Column("requested_by_employee_profile_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("owner_employee_profiles.id"), nullable=False),
        sa.Column("decided_by_staff_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("owner_staff_users.id")),
        sa.Column("decision_reason", sa.Text()),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_owner_expense_approvals_expense_id", "owner_expense_approvals", ["expense_id"])
    op.create_index("ix_owner_expense_approvals_status", "owner_expense_approvals", ["status"])
    op.create_index("ix_owner_expense_approvals_requested_by_employee_profile_id", "owner_expense_approvals", ["requested_by_employee_profile_id"])

    op.create_table(
        "owner_expense_payments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expense_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("owner_expenses.id"), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("payment_method", sa.String(32), nullable=False),
        sa.Column("payment_reference", sa.String(120)),
        sa.Column("status", sa.String(16), nullable=False, server_default="RECORDED"),
        sa.Column("idempotency_key", sa.String(128)),
        sa.Column("recorded_by_staff_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("owner_staff_users.id"), nullable=False),
        sa.Column("reversal_of_payment_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("owner_expense_payments.id")),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("idempotency_key", name="uq_expense_payment_idempotency_key"),
    )
    op.create_index("ix_owner_expense_payments_expense_id", "owner_expense_payments", ["expense_id"])

    op.create_table(
        "owner_expense_attachments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expense_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("owner_expenses.id"), nullable=False),
        sa.Column("storage_key", sa.String(256), nullable=False),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(127), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="ACTIVE"),
        sa.Column("uploaded_by_employee_profile_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("owner_employee_profiles.id"), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("storage_key", name="uq_expense_attachment_storage_key"),
    )
    op.create_index("ix_owner_expense_attachments_expense_id", "owner_expense_attachments", ["expense_id"])
    op.create_index("ix_owner_expense_attachments_content_hash", "owner_expense_attachments", ["content_hash"])

    op.create_table(
        "owner_cash_closings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("business_date", sa.Date(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="DRAFT"),
        sa.Column("opening_cash", sa.Numeric(12, 2), nullable=False),
        sa.Column("opening_cash_is_override", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("opening_cash_override_reason", sa.Text()),
        sa.Column("confirmed_cash_collections", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("confirmed_cash_refunds", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("cash_expense_payments", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("cash_commission_payouts", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("approved_cash_adjustments", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("expected_closing_cash", sa.Numeric(12, 2), nullable=False),
        sa.Column("actual_counted_cash", sa.Numeric(12, 2)),
        sa.Column("variance", sa.Numeric(12, 2)),
        sa.Column("variance_explanation", sa.Text()),
        sa.Column("prepared_by_staff_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("owner_staff_users.id"), nullable=False),
        sa.Column("reviewed_by_staff_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("owner_staff_users.id")),
        sa.Column("approved_by_staff_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("owner_staff_users.id")),
        sa.Column("submitted_at", sa.DateTime(timezone=True)),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.Column("reopen_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.UniqueConstraint("business_date", "currency", name="uq_cash_closing_scope"),
    )
    op.create_index("ix_owner_cash_closings_status", "owner_cash_closings", ["status"])

    op.create_table(
        "owner_cash_closing_adjustments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("cash_closing_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("owner_cash_closings.id"), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_by_staff_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("owner_staff_users.id"), nullable=False),
        sa.Column("approved_by_staff_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("owner_staff_users.id")),
    )
    op.create_index("ix_owner_cash_closing_adjustments_cash_closing_id", "owner_cash_closing_adjustments", ["cash_closing_id"])

    op.create_table(
        "owner_cash_closing_reopen_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("cash_closing_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("owner_cash_closings.id"), nullable=False),
        sa.Column("reopened_by_staff_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("owner_staff_users.id"), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("reopened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("prior_status", sa.String(20), nullable=False),
        sa.Column("prior_approved_by_staff_user_id", postgresql.UUID(as_uuid=True)),
        sa.Column("prior_approved_at", sa.DateTime(timezone=True)),
        sa.Column("prior_expected_closing_cash", sa.Numeric(12, 2), nullable=False),
        sa.Column("prior_actual_counted_cash", sa.Numeric(12, 2)),
        sa.Column("prior_variance", sa.Numeric(12, 2)),
    )
    op.create_index("ix_owner_cash_closing_reopen_events_cash_closing_id", "owner_cash_closing_reopen_events", ["cash_closing_id"])

    op.create_table(
        "owner_report_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("report_type", sa.String(40), nullable=False),
        sa.Column("scope", sa.String(64), nullable=False, server_default="GLOBAL"),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("currency", sa.String(3)),
        sa.Column("definition_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("snapshot_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(16), nullable=False, server_default="PUBLISHED"),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("generated_by", sa.String(16), nullable=False),
        sa.Column("generated_by_staff_user_id", postgresql.UUID(as_uuid=True)),
        sa.Column("cutoff_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("request_id", sa.String(64)),
        sa.Column("superseded_by_snapshot_id", postgresql.UUID(as_uuid=True)),
        sa.UniqueConstraint(
            "report_type", "scope", "period_start", "period_end", "currency",
            "definition_version", "snapshot_version",
            name="uq_report_snapshot_canonical_key",
        ),
    )
    op.create_index("ix_report_snapshots_type_status", "owner_report_snapshots", ["report_type", "status"])


def downgrade() -> None:
    op.drop_table("owner_report_snapshots")
    op.drop_table("owner_cash_closing_reopen_events")
    op.drop_table("owner_cash_closing_adjustments")
    op.drop_table("owner_cash_closings")
    op.drop_table("owner_expense_attachments")
    op.drop_table("owner_expense_payments")
    op.drop_table("owner_expense_approvals")

    op.drop_index("ix_owner_expenses_status", table_name="owner_expenses")
    op.drop_index("ix_owner_expenses_external_reference", table_name="owner_expenses")
    op.drop_index("ix_owner_expenses_category_id", table_name="owner_expenses")
    op.drop_index("ix_owner_expenses_entered_by_employee_profile_id", table_name="owner_expenses")
    op.drop_index("ix_owner_expenses_beneficiary_employee_profile_id", table_name="owner_expenses")
    op.drop_index("ix_owner_expenses_payee_id", table_name="owner_expenses")
    op.drop_constraint("uq_expense_number", "owner_expenses", type_="unique")
    op.drop_column("owner_expenses", "external_reference")
    op.drop_column("owner_expenses", "approved_amount")
    op.drop_column("owner_expenses", "beneficiary_employee_profile_id")
    op.drop_column("owner_expenses", "payee_id")
    op.drop_column("owner_expenses", "expense_number")

    op.drop_table("owner_expense_payees")
