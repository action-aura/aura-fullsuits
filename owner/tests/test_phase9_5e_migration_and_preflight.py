"""Phase 9.5E Milestones 19/25 -- physical PostgreSQL schema verification
(never trusting SQLAlchemy model index=True declarations alone) and the
blocking operational-finance domain-integrity preflight check."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from tests.conftest import make_staff

EXPECTED_INDEXES = {
    "owner_expense_payees": {"ix_owner_expense_payees_employee_profile_id"},
    "owner_expenses": {
        "ix_owner_expenses_payee_id", "ix_owner_expenses_beneficiary_employee_profile_id",
        "ix_owner_expenses_entered_by_employee_profile_id", "ix_owner_expenses_category_id",
        "ix_owner_expenses_external_reference", "ix_owner_expenses_status", "uq_expense_number",
    },
    "owner_expense_approvals": {
        "ix_owner_expense_approvals_expense_id", "ix_owner_expense_approvals_status",
        "ix_owner_expense_approvals_requested_by_employee_profile_id",
    },
    "owner_expense_payments": {"ix_owner_expense_payments_expense_id", "uq_expense_payment_idempotency_key"},
    "owner_expense_attachments": {
        "ix_owner_expense_attachments_expense_id", "ix_owner_expense_attachments_content_hash",
        "uq_expense_attachment_storage_key",
    },
    "owner_cash_closings": {"ix_owner_cash_closings_status", "uq_cash_closing_scope"},
    "owner_cash_closing_adjustments": {"ix_owner_cash_closing_adjustments_cash_closing_id"},
    "owner_cash_closing_reopen_events": {"ix_owner_cash_closing_reopen_events_cash_closing_id"},
    "owner_report_snapshots": {"ix_report_snapshots_type_status", "uq_report_snapshot_canonical_key"},
}


def test_every_declared_index_physically_exists_in_postgres(app):
    """Queries pg_indexes directly -- never trusts SQLAlchemy metadata, per
    the governing spec's own explicit instruction for this milestone."""
    from sqlalchemy import text
    from app.extensions import db_session

    with app.app_context():
        rows = db_session.execute(text("""
            SELECT tablename, indexname FROM pg_indexes
            WHERE tablename = ANY(:tables)
        """), {"tables": list(EXPECTED_INDEXES.keys())}).all()

    actual: dict[str, set[str]] = {}
    for tablename, indexname in rows:
        actual.setdefault(tablename, set()).add(indexname)

    missing = {}
    for table, expected_names in EXPECTED_INDEXES.items():
        present = actual.get(table, set())
        gap = expected_names - present
        if gap:
            missing[table] = gap

    assert missing == {}, f"index(es) declared in models but missing from the physical schema: {missing}"


def test_no_schema_drift_via_alembic_autogenerate(app):
    """Real alembic autogenerate-compare (not model introspection alone) --
    if this reports any operation, the models and the physical schema have
    drifted apart. Matches the exact technique used to build and verify
    migration e8fb57c82a58 in the first place."""
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from app.extensions import Base, db_session

    with app.app_context():
        conn = db_session.connection()
        context = MigrationContext.configure(conn)
        diff = compare_metadata(context, Base.metadata)

    assert diff == [], f"schema drift detected between models and the physical database: {diff}"


def _seed_active_employee(app, email, role_codes):
    from app.employees.services import activate_employee, create_employee_profile

    staff_id = make_staff(app, email, role_codes=role_codes)
    with app.app_context():
        profile = create_employee_profile(
            {"staff_user_id": staff_id, "employee_number": f"EMP-PF-{email[:8]}", "full_name": "Preflight Test", "employment_start_date": date(2026, 1, 1)},
            actor_staff_user_id=staff_id,
        )
        activate_employee(profile, actor_staff_user_id=staff_id)
        return staff_id, profile.id


def test_operational_finance_preflight_passes_on_healthy_data(app, seeded):
    from app.commercial_ops.preflight import run_preflight

    req_staff, req_profile = _seed_active_employee(app, "pf-healthy-req@example.com", ["SALES"])
    with app.app_context():
        result = run_preflight(key_directory=app.config["SIGNING_KEY_DIRECTORY"])
        finance_checks = [c for c in result.checks if "expense" in c.name or "cash_closing" in c.name or "management_note" in c.name]
        assert finance_checks, "no operational-finance preflight checks ran at all"
        failed = [c for c in finance_checks if c.status == "FAIL"]
        assert failed == [], f"preflight reported real failures on clean data: {failed}"


def test_operational_finance_preflight_catches_invalid_expense_status(app, seeded):
    """Proves the check is real -- not a check that always passes regardless
    of data. Directly corrupts a row (bypassing the service layer, exactly
    like a bad migration or a manual DB fix would) and confirms the
    preflight reports it."""
    from app.commercial_ops.preflight import run_preflight
    from app.expenses.lifecycle import create_expense
    from app.expenses.payees import create_payee
    from app.extensions import db_session
    from app.models.expenses import Expense, ExpenseCategory

    req_staff, req_profile = _seed_active_employee(app, "pf-corrupt-req@example.com", ["SALES"])
    with app.app_context():
        category = ExpenseCategory(category_code="PFCORRUPT", name="Preflight Corrupt", is_active=True)
        db_session.add(category)
        db_session.commit()
        payee = create_payee(payee_type="EXTERNAL", display_name="V", employee_profile_id=None, external_contact_reference=None, created_by_staff_user_id=req_staff)
        expense = create_expense(
            category_id=category.id, payee_id=payee.id, amount=Decimal("10.00"), currency="USD",
            expense_date=date(2026, 8, 1), description="corrupt test", external_reference=None,
            payment_method="CASH", payment_reference=None, entered_by_employee_profile_id=req_profile,
        )
        expense_id = expense.id
        # Bypass the service layer entirely -- a raw UPDATE, matching a bad
        # migration or a manual DB fix, not going through lifecycle.py.
        db_session.execute(
            Expense.__table__.update().where(Expense.id == expense_id).values(status="BOGUS_STATUS")
        )
        db_session.commit()

        result = run_preflight(key_directory=app.config["SIGNING_KEY_DIRECTORY"])
        status_check = next(c for c in result.checks if c.name == "no_invalid_expense_status")
        assert status_check.status == "FAIL"
        assert result.ok is False
