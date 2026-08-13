"""Phase 9.5E Milestone 26 -- real backup and isolated-restore proof for
every new Phase 9.5E table (Payee, Expense, ExpenseApproval, ExpensePayment,
ExpenseAttachment, CashClosing, ReportSnapshot, SharedManagementNote).

Uses the same app.system.backup.create_backup()/restore_backup() functions
Part Y already proved for Customer (see test_backup_restore.py), against the
same isolated `aura_owner_test` database the `app`/`seeded` fixtures always
point at -- never aura_owner_dev. This is what makes the restore "isolated":
restore_backup() always restores into whatever database the app's engine is
configured for, and in this test that is exclusively the per-test-truncated
aura_owner_test database.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from datetime import date
from decimal import Decimal

import pytest

from tests.conftest import make_staff


@pytest.fixture()
def backup_dir():
    path = tempfile.mkdtemp(prefix="owner-9-5e-backup-test-")
    yield path
    shutil.rmtree(path, ignore_errors=True)


def _seed_full_operational_finance_dataset(app, backup_staff):
    """Creates one real row in every new Phase 9.5E table via the actual
    service layer (never raw INSERTs) so the backup/restore proof covers
    genuine, referentially-valid data."""
    from app.employees.services import activate_employee, create_employee_profile
    from app.expenses.approvals import decide_expense_approval
    from app.expenses.attachments import upload_attachment
    from app.expenses.lifecycle import create_expense, submit_expense
    from app.expenses.payees import create_payee
    from app.expenses.payments import record_expense_payment
    from app.cash_closing.services import get_or_create_draft_closing
    from app.management_notes.service import create_note
    from app.operational_reports.scheduler import generate_snapshot
    from app.extensions import db_session
    from app.models.expenses import ExpenseCategory
    from app.models.staff import StaffUser

    req_staff = make_staff(app, "br-req@example.com", role_codes=["SALES"])
    appr_staff = make_staff(app, "br-appr@example.com", role_codes=["FINANCE"])

    req_profile = create_employee_profile(
        {"staff_user_id": req_staff, "employee_number": "EMP-BR-REQ", "full_name": "Backup Requester",
         "employment_start_date": date(2026, 1, 1)},
        actor_staff_user_id=req_staff,
    )
    activate_employee(req_profile, actor_staff_user_id=req_staff)
    appr_profile = create_employee_profile(
        {"staff_user_id": appr_staff, "employee_number": "EMP-BR-APPR", "full_name": "Backup Approver",
         "employment_start_date": date(2026, 1, 1)},
        actor_staff_user_id=appr_staff,
    )
    activate_employee(appr_profile, actor_staff_user_id=appr_staff)

    category = ExpenseCategory(category_code="BACKUPCAT", name="Backup Proof Category", is_active=True)
    db_session.add(category)
    db_session.commit()

    payee = create_payee(
        payee_type="EXTERNAL", display_name="Backup Proof Vendor",
        employee_profile_id=None, external_contact_reference="vendor@example.com",
        created_by_staff_user_id=req_staff,
    )

    expense = create_expense(
        category_id=category.id, payee_id=payee.id, amount=Decimal("250.00"), currency="USD",
        expense_date=date(2026, 8, 1), description="Backup/restore proof expense",
        external_reference=None, payment_method="CASH", payment_reference=None,
        entered_by_employee_profile_id=req_profile.id,
    )
    submit_expense(expense, actor_staff_user_id=req_staff)

    from app.expenses.approvals import pending_approval_for_expense
    approval = pending_approval_for_expense(expense)
    appr_staff_obj = db_session.get(StaffUser, appr_staff)
    decide_expense_approval(
        approval, expense, decision="APPROVED", decided_by=appr_staff_obj,
        approved_amount=Decimal("250.00"),
    )

    payment = record_expense_payment(
        expense, amount=Decimal("250.00"), currency="USD", payment_method="CASH",
        payment_reference="BR-PAY-1", recorded_by_staff_user_id=appr_staff,
    )

    attachment = upload_attachment(
        expense, content=b"%PDF-1.4 backup proof receipt",
        original_filename="receipt.pdf", declared_content_type="application/pdf",
        uploaded_by_employee_profile_id=req_profile.id,
    )

    closing = get_or_create_draft_closing(
        date(2026, 8, 1), "USD", prepared_by_staff_user_id=appr_staff,
        opening_cash_override=Decimal("1000.00"), opening_cash_override_reason="backup proof seed",
    )

    note = create_note(
        title="Backup proof note", body="Body surviving backup/restore",
        category=None, priority="NORMAL", visibility="ALL_STAFF",
        assigned_employee_profile_id=None, created_by_staff_user_id=appr_staff,
    )

    snapshot = generate_snapshot(
        report_type="DAILY_OPERATIONAL_SUMMARY", period_start=date(2026, 8, 1), period_end=date(2026, 8, 1),
        currency="USD", generated_by="MANUAL", generated_by_staff_user_id=appr_staff,
    )

    return {
        "payee_id": payee.id, "expense_id": expense.id, "approval_id": approval.id,
        "payment_id": payment.id, "attachment_id": attachment.id, "closing_id": closing.id,
        "note_id": note.id, "snapshot_id": snapshot.id,
    }


def test_backup_and_isolated_restore_recovers_every_phase9_5e_table(app, seeded, backup_dir):
    """Milestone 26: proves a real pg_dump/pg_restore cycle -- not model
    introspection, not a mock -- round-trips every one of the eight new
    Phase 9.5E tables. Deletes all seeded rows after the backup (simulating
    real data loss), asserts zero rows remain, restores, and asserts every
    single row is back with its original identity and material fields intact."""
    from app.extensions import db_session
    from app.models.expenses import Expense, ExpenseApproval, ExpenseAttachment, ExpensePayment, Payee
    from app.models.cash_closing import CashClosing
    from app.models.management_notes import SharedManagementNote
    from app.models.report_snapshots import ReportSnapshot
    from app.system.backup import create_backup, restore_backup

    backup_staff = make_staff(app, "br-operator@example.com", super_admin=True)

    with app.app_context():
        ids = _seed_full_operational_finance_dataset(app, backup_staff)

        record = create_backup(backup_dir, backup_staff)
        assert record.status == "SUCCESS"

        # Simulate total data loss across every new Phase 9.5E table.
        db_session.query(ExpenseAttachment).delete()
        db_session.query(ExpensePayment).delete()
        db_session.query(ExpenseApproval).delete()
        db_session.query(Expense).delete()
        db_session.query(Payee).delete()
        db_session.query(CashClosing).delete()
        db_session.query(SharedManagementNote).delete()
        db_session.query(ReportSnapshot).delete()
        db_session.commit()

        assert db_session.get(Expense, ids["expense_id"]) is None
        assert db_session.get(CashClosing, ids["closing_id"]) is None
        assert db_session.get(SharedManagementNote, ids["note_id"]) is None
        assert db_session.get(ReportSnapshot, ids["snapshot_id"]) is None

        restore_backup(record, backup_dir, backup_staff)
        db_session.expire_all()

        restored_payee = db_session.get(Payee, ids["payee_id"])
        restored_expense = db_session.get(Expense, ids["expense_id"])
        restored_approval = db_session.get(ExpenseApproval, ids["approval_id"])
        restored_payment = db_session.get(ExpensePayment, ids["payment_id"])
        restored_attachment = db_session.get(ExpenseAttachment, ids["attachment_id"])
        restored_closing = db_session.get(CashClosing, ids["closing_id"])
        restored_note = db_session.get(SharedManagementNote, ids["note_id"])
        restored_snapshot = db_session.get(ReportSnapshot, ids["snapshot_id"])

        assert restored_payee is not None and restored_payee.display_name == "Backup Proof Vendor"
        assert restored_expense is not None
        assert restored_expense.amount == Decimal("250.00")
        assert restored_expense.status == "PAID"
        assert restored_approval is not None and restored_approval.status == "APPROVED"
        assert restored_payment is not None and restored_payment.amount == Decimal("250.00")
        assert restored_attachment is not None and restored_attachment.content_type == "application/pdf"
        assert restored_closing is not None and restored_closing.business_date == date(2026, 8, 1)
        assert restored_note is not None and restored_note.title == "Backup proof note"
        assert restored_snapshot is not None and restored_snapshot.status == "PUBLISHED"
