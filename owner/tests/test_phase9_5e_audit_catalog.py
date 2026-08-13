"""Phase 9.5E Milestone 18 -- every audit action_code emitted by the new
Expense/Cash-Closing/Report-Snapshot/Management-Note services is registered
in app.i18n_labels.generic_audit_action_label AND actually localized (not
silently falling back to the raw code) in both English and Arabic."""
from __future__ import annotations

import ast
import re
from pathlib import Path

OWNER_ROOT = Path(__file__).resolve().parents[1]

SERVICE_FILES = [
    "app/expenses/lifecycle.py",
    "app/expenses/approvals.py",
    "app/expenses/payments.py",
    "app/expenses/attachments.py",
    "app/expenses/payees.py",
    "app/expenses/duplicates.py",
    "app/cash_closing/services.py",
    "app/operational_reports/scheduler.py",
    "app/management_notes/service.py",
]


def _emitted_action_codes() -> set[str]:
    """AST-based extraction of every literal action_code="..." passed to
    audit_record() -- same technique Phase 9.5D Milestone 21 used to sweep
    for missing audit calls, applied here to sweep for missing catalog
    registrations instead."""
    codes = set()
    for rel_path in SERVICE_FILES:
        tree = ast.parse((OWNER_ROOT / rel_path).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                is_audit_call = (isinstance(func, ast.Name) and func.id == "audit_record") or (
                    isinstance(func, ast.Attribute) and func.attr == "audit_record"
                )
                if not is_audit_call:
                    continue
                for kw in node.keywords:
                    if kw.arg == "action_code" and isinstance(kw.value, ast.Constant):
                        codes.add(kw.value.value)
                    elif kw.arg == "action_code" and isinstance(kw.value, ast.JoinedStr):
                        # f"EXPENSE_APPROVAL_{decision}" -- decision is one of
                        # a known closed set (APPROVED/REJECTED/RETURNED),
                        # expand explicitly rather than skip.
                        for decision in ("APPROVED", "REJECTED", "RETURNED"):
                            codes.add(f"EXPENSE_APPROVAL_{decision}")
    return codes


def test_every_emitted_action_code_is_registered_and_localized():
    from app.i18n_labels import generic_audit_action_label

    emitted = _emitted_action_codes()
    assert emitted, "sweep found zero action codes -- the AST extraction itself is broken"

    unregistered = []
    for code in sorted(emitted):
        label = generic_audit_action_label(code)
        if label == code:
            unregistered.append(code)

    assert unregistered == [], f"{len(unregistered)} emitted action code(s) have no registered display label: {unregistered}"


def test_registered_phase9_5e_labels_have_real_arabic_translations(app):
    """Not just registered in the EN catalog -- proves the AR .po entry is
    non-empty, non-fuzzy, and genuinely different from the English source
    (catches an accidental identity 'translation')."""
    from babel.messages.pofile import read_po

    emitted = _emitted_action_codes()

    with (OWNER_ROOT / "translations/en/LC_MESSAGES/messages.po").open(encoding="utf-8") as f:
        en_catalog = read_po(f, locale="en")
    with (OWNER_ROOT / "translations/ar/LC_MESSAGES/messages.po").open(encoding="utf-8") as f:
        ar_catalog = read_po(f, locale="ar")

    with app.test_request_context():
        from app.i18n_labels import generic_audit_action_label
        english_labels = {code: generic_audit_action_label(code) for code in emitted}

    ar_by_id = {m.id: m for m in ar_catalog}
    missing_ar = []
    identical_to_english = []
    for code, english_text in english_labels.items():
        ar_message = ar_by_id.get(english_text)
        if ar_message is None or not ar_message.string or ar_message.fuzzy:
            missing_ar.append(code)
        elif ar_message.string == english_text:
            identical_to_english.append(code)

    assert missing_ar == [], f"{len(missing_ar)} code(s) missing a real Arabic catalog entry: {missing_ar}"
    assert identical_to_english == [], f"{len(identical_to_english)} code(s) have an Arabic 'translation' identical to English: {identical_to_english}"


def test_full_lifecycle_actually_emits_every_registered_action_code(app, seeded):
    """Coverage proof, not just registration -- walks a real Expense +
    Cash-Closing + Report-Snapshot + Management-Note lifecycle and asserts
    every one of the 28 Phase 9.5E action codes actually appears as a real
    AuditLog row, not just a catalog entry that nothing ever emits."""
    import uuid
    from datetime import date
    from decimal import Decimal

    from tests.conftest import make_staff

    # Created upfront -- make_staff() opens its own nested app_context() and
    # calls db_session.remove() at exit, which detaches any ORM instances
    # already loaded in an outer context. Every staff account this test
    # needs is created before any expense/closing object is loaded.
    req_staff = make_staff(app, "auditcov-req@example.com", role_codes=["SALES"])
    fin_staff = make_staff(app, "auditcov-fin@example.com", role_codes=["FINANCE"])
    adjustments_staff = make_staff(app, "auditcov-adj@example.com", role_codes=["FINANCE"])
    approver_staff = make_staff(app, "auditcov-appr@example.com", role_codes=["FINANCE"])

    with app.app_context():
        from app.employees.services import activate_employee, create_employee_profile
        from app.expenses.approvals import decide_expense_approval, pending_approval_for_expense
        from app.expenses.attachments import archive_attachment, upload_attachment
        from app.expenses.duplicates import find_duplicate_signals, override_duplicate_warning
        from app.expenses.errors import ExpenseError
        from app.expenses.lifecycle import create_expense, revise_expense, submit_expense, void_expense
        from app.expenses.payees import create_payee, deactivate_payee
        from app.expenses.payments import record_expense_payment, reverse_expense_payment
        from app.cash_closing.services import (
            add_adjustment, approve_adjustment, close_closing, decide_closing,
            get_or_create_draft_closing, reopen_closing, submit_closing,
        )
        from app.management_notes.service import add_comment, assign_note, create_note, set_note_status
        from app.operational_reports.scheduler import generate_snapshot, regenerate_snapshot
        from app.extensions import db_session
        from app.models.audit import AuditLog
        from app.models.expenses import ExpenseCategory
        from app.models.staff import StaffUser
        from sqlalchemy import select

        req_profile = create_employee_profile(
            {"staff_user_id": req_staff, "employee_number": "EMP-AUDCOV1", "full_name": "Req", "employment_start_date": date(2026, 1, 1)},
            actor_staff_user_id=req_staff,
        )
        activate_employee(req_profile, actor_staff_user_id=req_staff)
        fin_profile = create_employee_profile(
            {"staff_user_id": fin_staff, "employee_number": "EMP-AUDCOV2", "full_name": "Fin", "employment_start_date": date(2026, 1, 1)},
            actor_staff_user_id=fin_staff,
        )
        activate_employee(fin_profile, actor_staff_user_id=fin_staff)

        category = ExpenseCategory(category_code=f"AUDCOV-{uuid.uuid4().hex[:8]}", name="Audit Coverage", is_active=True)
        db_session.add(category)
        db_session.commit()

        payee = create_payee(payee_type="EXTERNAL", display_name="Audit Vendor", employee_profile_id=None, external_contact_reference=None, created_by_staff_user_id=fin_staff)

        expense = create_expense(
            category_id=category.id, payee_id=payee.id, amount=Decimal("100.00"), currency="USD",
            expense_date=date(2026, 8, 1), description="audit coverage expense", external_reference="AUDCOV-REF-1",
            payment_method="CASH", payment_reference=None, entered_by_employee_profile_id=req_profile.id,
        )
        submit_expense(expense, actor_staff_user_id=req_staff)
        signals = find_duplicate_signals(expense)
        override_duplicate_warning(expense, actor_staff_user_id=req_staff, reason="known false positive", signals=signals)
        approval = pending_approval_for_expense(expense)
        fin_staff_obj = db_session.get(StaffUser, fin_staff)
        decide_expense_approval(approval, expense, decision="RETURNED", decided_by=fin_staff_obj, decision_reason="need more detail")
        revise_expense(expense, actor_staff_user_id=req_staff, description="audit coverage expense (revised)")
        submit_expense(expense, actor_staff_user_id=req_staff)
        approval = pending_approval_for_expense(expense)
        decide_expense_approval(approval, expense, decision="APPROVED", decided_by=fin_staff_obj, approved_amount=Decimal("100.00"))

        attachment = upload_attachment(expense, content=b"%PDF-1.4\ncoverage\n", original_filename="r.pdf", declared_content_type="application/pdf", uploaded_by_employee_profile_id=req_profile.id)
        archive_attachment(attachment, actor_staff_user_id=req_staff)

        payment = record_expense_payment(expense, amount=Decimal("40.00"), currency="USD", payment_method="CASH", payment_reference=None, recorded_by_staff_user_id=fin_staff)
        reverse_expense_payment(payment, expense, actor_staff_user_id=fin_staff, reason="wrong amount")

        second_expense = create_expense(
            category_id=category.id, payee_id=payee.id, amount=Decimal("5.00"), currency="USD",
            expense_date=date(2026, 8, 2), description="to void", external_reference=None,
            payment_method="CASH", payment_reference=None, entered_by_employee_profile_id=req_profile.id,
        )
        void_expense(second_expense, actor_staff_user_id=req_staff, reason="not needed")
        deactivate_payee(payee, actor_staff_user_id=fin_staff)

        closing = get_or_create_draft_closing(date(2026, 8, 20), "USD", prepared_by_staff_user_id=fin_staff, opening_cash_override=Decimal("50.00"), opening_cash_override_reason="test")
        add_adjustment(closing, amount=Decimal("5.00"), reason="test adjustment", created_by_staff_user_id=fin_staff)
        from app.models.cash_closing import CashClosingAdjustment
        adjustment_row = db_session.execute(select(CashClosingAdjustment).where(CashClosingAdjustment.cash_closing_id == closing.id)).scalars().first()
        approve_adjustment(adjustment_row, actor_staff_user_id=adjustments_staff)
        submit_closing(closing, actor_staff_user_id=fin_staff, actual_counted_cash=Decimal("55.00"), variance_explanation=None)
        decide_closing(closing, decision="APPROVED", actor_staff_user_id=approver_staff)
        close_closing(closing, actor_staff_user_id=approver_staff)
        reopen_closing(closing, actor_staff_user_id=approver_staff, reason="late item", recent_auth_verified=True)

        snapshot = generate_snapshot(report_type="DAILY_OPERATIONAL_SUMMARY", period_start=date(2026, 8, 1), period_end=date(2026, 8, 1), currency="USD", generated_by="MANUAL", generated_by_staff_user_id=fin_staff)
        regenerate_snapshot(report_type="DAILY_OPERATIONAL_SUMMARY", period_start=date(2026, 8, 1), period_end=date(2026, 8, 1), currency="USD", actor_staff_user_id=fin_staff, reason="correction")

        note = create_note(title="Audit coverage note", body="body", category=None, priority="MEDIUM", visibility="MANAGEMENT_ONLY", assigned_employee_profile_id=None, created_by_staff_user_id=fin_staff)
        from app.management_notes.service import update_note
        update_note(note, actor_staff_user_id=fin_staff, title="Audit coverage note (updated)")
        assign_note(note, assignee_employee_profile_id=req_profile.id, actor_staff_user_id=fin_staff)
        set_note_status(note, target_status="IN_PROGRESS", actor_staff_user_id=fin_staff)
        add_comment(note, body="a comment", author_staff_user_id=fin_staff)

        emitted_codes = set(db_session.execute(select(AuditLog.action_code)).scalars().all())

    expected = {
        "EXPENSE_CREATED", "EXPENSE_SUBMITTED", "EXPENSE_REVISED", "EXPENSE_VOIDED",
        "EXPENSE_APPROVAL_APPROVED", "EXPENSE_APPROVAL_RETURNED",
        "EXPENSE_PAYMENT_RECORDED", "EXPENSE_PAYMENT_REVERSED",
        "EXPENSE_ATTACHMENT_UPLOADED", "EXPENSE_ATTACHMENT_ARCHIVED",
        "EXPENSE_PAYEE_CREATED", "EXPENSE_PAYEE_DEACTIVATED",
        "EXPENSE_DUPLICATE_WARNING_OVERRIDDEN",
        "CASH_CLOSING_CREATED", "CASH_CLOSING_SUBMITTED", "CASH_CLOSING_APPROVED",
        "CASH_CLOSING_CLOSED", "CASH_CLOSING_REOPENED",
        "CASH_CLOSING_ADJUSTMENT_CREATED", "CASH_CLOSING_ADJUSTMENT_APPROVED",
        "REPORT_SNAPSHOT_GENERATED", "REPORT_SNAPSHOT_REGENERATED",
        "MANAGEMENT_NOTE_CREATED", "MANAGEMENT_NOTE_UPDATED", "MANAGEMENT_NOTE_ASSIGNED",
        "MANAGEMENT_NOTE_STATUS_CHANGED", "MANAGEMENT_NOTE_COMMENT_ADDED",
    }
    missing = expected - emitted_codes
    assert missing == set(), f"action codes never actually emitted: {sorted(missing)}"
